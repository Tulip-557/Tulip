#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
echo_audit.py — 来源回声核查：抓取路书引用的网页，验证「声称的值真的在页面上」。

为什么需要它
------------
本 skill 的「内容真实」缺口分两半：
  · 声明 ↔ 采集留痕      —— claim_audit.py 已做（实采 16 写成 15 会红）
  · 声明 ↔ 来源网页      —— 没人管。线索卡里每条 claim 都带着 URL 和核实日期，
    但「页面里真有这句话 / 这个数」从来没有机器验证过。本工具补这一层（v1）：
    把线索卡 claim 里可机械比对的值（数字 / 时刻 / 关键短语）拿到**现在抓回来的
    页面正文**里找——页面上找不到，就是「来源不回声」。

判定分级（每条 claim）
----------------------
  SUPPORTED     页面里找得到声称的值 / 关键短语
  PARTIAL       部分可寻（一句多个值只找到一部分）
  UNSUPPORTED   页面抓到了，但什么都找不到——来源不回声
  UNREACHABLE   抓不到（403 / 风控 / 超时 / DNS）——不是「没有」，是「看不到」
  SKIP          动态源（地图 / 12306，归 claim_audit 管）、无数值且关键短语
                不足、或本轮抓取预算用尽

力度（与 E4 死线哲学对齐，宁可WARN不可错杀）
--------------------------
  · 死线类 claim（票价 / 时刻 / 车程距离信号）且 UNSUPPORTED → **FAIL**
    ——这几个字段写进路书就要求 [A] 级实证，来源页里却找不到，没有第二条路。
  · 其余 UNSUPPORTED / UNREACHABLE → WARN（点名 + 给重查出路），不阻断。
    误报源是真实的：页面在核实日期之后合法改版、动态价格浮动、反爬拦截。
    机器分不清「抄错」和「后来变了」，所以只对钉得住的那一半判死。

不查（边界，写在这里是为了不被读成保证）
--------------------------------------
· 页面是 JS 渲染、正文拿不到的 → UNREACHABLE，不判死
· 页面语义改写（同一事实换个说法）——短语匹配兜不住，归人工
· 抓取只读、单次、每域限量，不做增量监控——变更监控归 bulletin（规划中）
· 「抓到了且匹配」不保证页面对——引用页本身错了，机器无从知道

用法
----
    python tools/echo_audit.py --clues 社媒线索卡_X.json [--facts 路书_X.json]
                               [--cache 缓存目录] [--timeout 15] [--json]
退出码：0 = 无 FAIL ｜ 2 = 有 FAIL ｜ 1 = 用法/读取错误
离线（无网络）= SKIP 全部并退出 0，但明确打印「离线跳过」，不假装查过。
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# ------------------------------------------------------------ 常量

UA = 'verified-travel-planner-echo/1.0 (+https://github.com/Tulip-557/verified-travel-planner)'

#: 动态/地图类域名：数值实时变化，回声比对没有意义——归 claim_audit 的留痕比对管。
SKIP_DOMAINS = (
    'amap.com', 'map.baidu.com', 'map.qq.com', '12306.cn',
    'flights.ctrip.com', 'hotels.ctrip.com',
)

#: 命中即视为风控/拦截页，正文不可信 → UNREACHABLE。
RISK_MARKS = ('验证码', '访问验证', '访问异常', 'captcha', 'Captcha', 'CAPTCHA',
              '滑动验证', '安全验证', '请完成验证')

#: 死线信号——与 source_audit.py E4 的口径保持同步（改动两头改）。
PRICE_SIG = re.compile(r'[¥￥]\s*\d|\d+\s*元|免费|免票')
HOURS_SIG = re.compile(r'\d{1,2}[:：]\d{2}\s*[-–~—至]\s*\d{1,2}[:：]\d{2}'
                       r'|\d{1,2}[:：]\d{2}')
ROUTE_RE = re.compile(
    r'\d+(?:\.\d+)?\s*公里'
    r'|\d+\s*分钟\s*/\s*\d'
    r'|(?:打车|车程|步行|驾车|骑行|公交)\D{0,8}\d+\s*分钟')
RAIL_RE = re.compile(r'[GGKKTZ]\d{1,4}|余票|席别')
DEADLINE_HINT = re.compile(
    r'闭馆|停止|开放时间|营业|票价|门票|余票|末班|首班|发车')

NUM_UNIT_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*(万元|元|公里|千米|千米/小时|公里/小时|米|分钟|小时)')
TIME_RE = re.compile(r'\d{1,2}[:：]\d{2}')
# 中文数字 + 单位（「四十二分钟」「四十余公里」）：政务/官方页极常用，
# 只抓阿拉伯数字会让这一半的关键值漏出核查面。
CN_NUM_UNIT_RE = re.compile(
    r'([零〇一二两三四五六七八九十百]+)(?:余|多)?\s*(万元|元|公里|千米|米|分钟|小时)')
CN_DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
             '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def cn_to_int(s: str):
    """中文数字 → int（支持到 999：四十二 / 两百零五）。不合法返回 None。"""
    if not s:
        return None
    if '百' in s:
        head, _, tail = s.partition('百')
        h = CN_DIGITS.get(head, 1) if head else 1
        if h is None:
            return None
        return h * 100 + (cn_to_int(tail) or 0)
    if '十' in s:
        head, _, tail = s.partition('十')
        t = CN_DIGITS.get(head, 1) if head else 1
        if t is None:
            return None
        return t * 10 + (CN_DIGITS.get(tail, 0) if tail else 0)
    return CN_DIGITS.get(s)

#: 数字无单位的关键句兜底：短语回声所需的最长公共片段长度。
ECHO_STRONG = 6     # ≥6 字连续相同 → SUPPORTED（官方页面几乎总会原句收录）
ECHO_WEAK = 3       # ≥3 字 → PARTIAL

MAX_FETCH = 12      # 单轮抓取预算：礼貌上限，用尽的 claim 记 SKIP
FETCH_GAP_SEC = 0.8

_SYM_FALLBACK = str.maketrans({
    '✅': '[OK]', '✓': '[OK]', '❌': '[X]', '✗': '[X]', '⚠': '[!]', '｜': '|',
})


def emit(line=''):
    try:
        print(line)
    except UnicodeEncodeError:
        try:
            print(line.translate(_SYM_FALLBACK))
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or 'ascii'
            print(line.encode(enc, 'replace').decode(enc))


# ------------------------------------------------------------ 文本规整

def strip_html(s: str) -> str:
    s = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', s)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = s.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<') \
         .replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'")
    s = unicodedata.normalize('NFKC', s)
    return re.sub(r'\s+', ' ', s)


def norm_num(s: str) -> str:
    """数字规整：去千分位、全角转半角、去尾零。"""
    s = unicodedata.normalize('NFKC', s).replace(',', '').replace('，', '')
    try:
        f = float(s)
    except ValueError:
        return s
    out = ('%f' % f).rstrip('0').rstrip('.')
    return out if out else '0'


def claim_values(text: str) -> list:
    """claim 里的可核查值：[(数值规整形, 单位), …]。时刻与中文数字都抓。"""
    vals = []
    for m in NUM_UNIT_RE.finditer(text):
        vals.append((norm_num(m.group(1)), m.group(2)))
    for m in CN_NUM_UNIT_RE.finditer(text):
        n = cn_to_int(m.group(1))
        if n is not None:
            vals.append((str(n), m.group(2)))
    for m in TIME_RE.finditer(text):
        t = unicodedata.normalize('NFKC', m.group(0)).replace('：', ':')
        vals.append((t, '时刻'))
    return vals


def is_deadline(text: str) -> bool:
    """死线类判定——口径对齐 source_audit E4：票价/时刻/车程/余票。

    时刻与价格须带语境（开放/闭馆/门票/余票…），裸数字时间与普通
    消费金额不算——「07:40 珠海站集合」「人均 60」不该被当死线。
    """
    if RAIL_RE.search(text) and ('余票' in text or '席别' in text
                                 or '车次' in text):
        return True
    cn_price = CN_NUM_UNIT_RE.search(text) and '元' in text
    if DEADLINE_HINT.search(text) and (PRICE_SIG.search(text)
                                       or HOURS_SIG.search(text) or cn_price):
        return True
    if ROUTE_RE.search(text):
        return True
    return False


# ------------------------------------------------------------ 抓取

class Fetcher:
    """极简只读抓取器：限量、限速、落缓存（供 cross_check 复用）。"""

    def __init__(self, cache_dir: Path = None, timeout: int = 15,
                 budget: int = MAX_FETCH, gap: float = FETCH_GAP_SEC):
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.left = budget
        self.gap = gap
        self.offline = None      # None=未测；True=判定离线

    def _cache_path(self, url: str) -> Path:
        h = hashlib.sha256(url.encode('utf-8')).hexdigest()[:20]
        return (self.cache_dir / (h + '.json')) if self.cache_dir else None

    def fetch(self, url: str) -> dict:
        """返回 {status: OK/UNREACHABLE/RISK, text, note}。命中缓存直接回。"""
        cp = self._cache_path(url)
        if cp and cp.is_file():
            try:
                return json.loads(cp.read_text(encoding='utf-8'))
            except ValueError:
                pass
        if self.offline:
            return {'status': 'OFFLINE', 'text': '', 'note': '离线环境'}
        if self.left <= 0:
            return {'status': 'BUDGET', 'text': '', 'note': '本轮抓取预算用尽'}
        self.left -= 1
        if self.gap:
            time.sleep(self.gap)
        req = urllib.request.Request(url, headers={
            'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9'})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                ctype = str(resp.headers.get('Content-Type') or '')
                raw = resp.read(2_000_000).decode('utf-8', 'replace')
                final_url = resp.geturl()
        except (urllib.error.URLError, TimeoutError, OSError,
                ValueError) as exc:
            res = {'status': 'UNREACHABLE', 'text': '',
                   'note': str(exc)[:120]}
            self._save(cp, url, res)
            return res
        if 'html' not in ctype and 'text' not in ctype and ctype:
            res = {'status': 'UNREACHABLE', 'text': '',
                   'note': '非文本类型：%s' % ctype[:60]}
            self._save(cp, url, res)
            return res
        text = strip_html(raw)
        if any(m in text for m in RISK_MARKS):
            res = {'status': 'RISK', 'text': '',
                   'note': '命中风控特征，正文不可信'}
            self._save(cp, url, res)
            return res
        res = {'status': 'OK', 'text': text, 'note': '', 'final_url': final_url}
        self._save(cp, url, res)
        return res

    @staticmethod
    def _save(cp, url, res):
        if not cp:
            return
        try:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(
                {'url': url, **res}, ensure_ascii=False), encoding='utf-8')
        except OSError:
            pass


# ------------------------------------------------------------ 核查

def int_to_cn(n: int) -> str:
    """int → 中文数字（1-999），供页面侧变体匹配：claim 写 42、页面写四十二。"""
    digits = '零一二三四五六七八九'
    if n >= 100:
        h, rest = divmod(n, 100)
        out = digits[h] + '百'
        if rest == 0:
            return out
        if rest < 10:
            return out + '零' + digits[rest]
        return out + int_to_cn(rest)
    if n >= 10:
        t, o = divmod(n, 10)
        return (digits[t] if t > 1 else '') + '十' + (digits[o] if o else '')
    return digits[n]


def _num_in_page(num: str, page: str) -> bool:
    """数值在页中：两侧不得紧跟数字或小数点——防「16」撞上「2016」「16.5」。

    中文数字页面（「四十二分钟」）与阿拉伯页面（「42分钟」）互为变体，
    两边都试——只认一种写法会把「写法差异」误判成「来源不回声」。
    （时刻/冒号不在此列：'16' 匹配到 '16:00' 属可接受的一次放宽，
    因为页面几乎总把时刻连着上下文一起原句收录，短语回声会再兜一层。）
    """
    variants = [num]
    if num.isdigit():
        n = int(num)
        if 1 <= n <= 999:
            variants.append(int_to_cn(n))
    for v in variants:
        if re.search(r'(?<![\d.])' + re.escape(v) + r'(?![\d.])', page):
            return True
    return False


def check_claim(claim_text: str, page_text: str) -> dict:
    """对单条 claim 与页面正文做回声比对，返回 {verdict, detail}。"""
    vals = claim_values(claim_text)
    if vals:
        hits, miss = [], []
        for num, unit in vals:
            km = None
            if unit == '米':
                try:
                    km = norm_num(str(float(num) / 1000))
                except ValueError:
                    km = None
            found = _num_in_page(num, page_text) or (
                km is not None and _num_in_page(km, page_text))
            (hits if found else miss).append('%s%s' % (num, unit))
        if not miss:
            return {'verdict': 'SUPPORTED', 'detail': '值全可寻：%s' % '、'.join(hits)}
        if hits:
            return {'verdict': 'PARTIAL', 'detail': '可寻 %s；缺 %s'
                    % ('、'.join(hits), '、'.join(miss))}
        return {'verdict': 'UNSUPPORTED', 'detail': '值均不可寻：%s'
                % '、'.join(miss)}
    # 无数值 claim：关键短语回声——官方页几乎总会原句收录核心句
    m = difflib.SequenceMatcher(None, claim_text, page_text) \
        .find_longest_match(0, len(claim_text), 0, len(page_text))
    n = m.size
    if n >= ECHO_STRONG:
        return {'verdict': 'SUPPORTED', 'detail': '关键短语回声 %d 字：%s'
                % (n, claim_text[m.a:m.a + n])}
    if n >= ECHO_WEAK:
        return {'verdict': 'PARTIAL', 'detail': '短语部分回声 %d 字：%s'
                % (n, claim_text[m.a:m.a + n])}
    return {'verdict': 'UNSUPPORTED', 'detail': '正文里找不到任何 ≥%d 字的共现片段'
            % ECHO_WEAK}


def collect_claims(clues: dict) -> list:
    """从线索卡抽 (claim 文本, url, 核实日期)。url 缺失的 claim 不在核查面。"""
    out, seen = [], set()
    for note in clues.get('notes') or []:
        if not isinstance(note, dict):
            continue
        url = str(note.get('url') or '').strip()
        if not url:
            continue
        texts = [str(c.get('text') or '') for c in (note.get('claims') or [])
                 if isinstance(c, dict)]
        for ev in (note.get('place_evidence') or []):
            if isinstance(ev, dict):
                texts.append(str(ev.get('text') or ''))
        for t in texts:
            t = t.strip()
            # 同一 URL 下「claims 与 place_evidence 互相重复」是常态——
            # 同一条claim抓两次、报两遍，只会把计数搅浑。
            key = (t, url)
            if t and key not in seen:
                seen.add(key)
                out.append({'text': t, 'url': url,
                            'checked_at': str(note.get('checked_at') or ''),
                            'title': str(note.get('title') or '')})
    return out


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='来源回声核查：抓取引用页，验证声称的值真的在页面上（第2层）')
    ap.add_argument('--clues', required=True, help='社媒线索卡 JSON（claims 带 URL）')
    ap.add_argument('--facts', help='事实源 JSON（当前仅用于报告语境，v1 不参与判定）')
    ap.add_argument('--cache', help='抓取缓存目录（供 cross_check 复用；建议给临时目录）')
    ap.add_argument('--timeout', type=int, default=15, help='单页抓取超时秒数')
    ap.add_argument('--budget', type=int, default=MAX_FETCH,
                    help='单轮抓取页数上限（礼貌预算）')
    ap.add_argument('--json', action='store_true', help='机读输出')
    a = ap.parse_args(argv)

    clues_path = Path(a.clues)
    if not clues_path.is_file():
        emit('线索卡不存在：%s' % clues_path)
        return 1
    try:
        clues = json.loads(clues_path.read_text(encoding='utf-8'))
    except ValueError as exc:
        emit('线索卡解析失败：%s' % exc)
        return 1

    claims = collect_claims(clues)
    if not claims:
        emit('线索卡里没有带 URL 的 claim——回声核查无事可做（这本身值得警惕：'
             '所有说法都给不出来源页）')
        return 0

    cache_dir = Path(a.cache) if a.cache else None
    fetcher = Fetcher(cache_dir=cache_dir, timeout=a.timeout, budget=a.budget)

    rows = []
    n_fail = n_warn = 0
    for c in claims:
        dom = domain_of(c['url'])
        row = {'claim': c['text'][:80], 'url': c['url'], 'domain': dom,
               'checked_at': c['checked_at']}
        if any(dom.endswith(d) or d in dom for d in SKIP_DOMAINS):
            row.update(verdict='SKIP', detail='动态/地图源——数值比对归 claim_audit 留痕面')
        else:
            page = fetcher.fetch(c['url'])
            if page['status'] == 'OFFLINE':
                row.update(verdict='SKIP', detail='离线环境，跳过（不假装查过）')
            elif page['status'] == 'BUDGET':
                row.update(verdict='SKIP', detail=page['note'])
            elif page['status'] != 'OK':
                row.update(verdict='UNREACHABLE', detail=page['note'])
            else:
                res = check_claim(c['text'], page['text'])
                row.update(verdict=res['verdict'], detail=res['detail'])
        if row['verdict'] == 'UNSUPPORTED' and is_deadline(c['text']):
            n_fail += 1
        elif row['verdict'] in ('UNSUPPORTED', 'UNREACHABLE', 'PARTIAL'):
            n_warn += 1
        rows.append(row)

    result = {
        'clues': str(clues_path),
        'claims_total': len(claims),
        'rows': rows,
        'fail': n_fail,
        'warn': n_warn,
        'ok': n_fail == 0,
        'scope': ('v1 只核查线索卡 claim 的数值与关键短语是否在引用页正文中'
                  '回声；语义改写、JS 渲染页、页面本身的内容错误不在能力内。'),
        'not_checked': ('页面语义等价改写；页面在核实日期之后的变化是否合法；'
                        '引用页自身内容是否正确（链条止于「页面怎么说」）。'),
    }

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0 if result['ok'] else 2

    emit('=' * 74)
    emit('来源回声核查（echo_audit v1）｜ 线索卡：%s ｜ claim %d 条'
         % (clues_path.name, len(claims)))
    emit('=' * 74)
    marks = {'SUPPORTED': '[OK]', 'PARTIAL': '[~ ]', 'UNSUPPORTED': '[X ]',
             'UNREACHABLE': '[? ]', 'SKIP': '[--]'}
    for r in rows:
        emit('  %s %-12s %s' % (marks[r['verdict']], r['verdict'],
                                r['claim'][:56]))
        emit('       %s ｜ %s' % (r['domain'], r['detail'][:72]))
    emit('-' * 74)
    for v in ('SUPPORTED', 'PARTIAL', 'UNSUPPORTED', 'UNREACHABLE', 'SKIP'):
        n = sum(1 for r in rows if r['verdict'] == v)
        if n:
            emit('  %-12s %d' % (v, n))
    if n_fail:
        emit('[X] FAIL %d 处——死线类声明在引用页里找不到值，先重查或改写再交付'
             % n_fail)
        emit('    误报说明：若页面在核实日期之后合法改版，更新核查日期并重查。')
        return 2
    emit('[OK] 回声核查无 FAIL（WARN %d 处是点名清单，不阻断）' % n_warn)
    emit('    ⚠️ 「页面里有」≠「页面说的对」——本闸门只证明回声，不证明真理。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
