#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
echo_audit.py — 来源回声核查：抓取路书引用的网页，验证「声称的值真的在页面上」。

为什么需要它
------------
本 skill 的「内容真实」缺口分两半：
  · 声明 ↔ 采集留痕      —— claim_audit.py 已做（实采 16 写成 15 会红）
  · 声明 ↔ 来源网页      —— 没人管。线索卡里每条 claim 都带着 URL 和核实日期，
    但「页面里真有这句话 / 这个数」从来没有机器验证过。本工具补这一层：
    把 claim 里可机械比对的值（数字 / 时刻 / 关键短语）拿到**现在抓回来的
    页面正文**里找——页面上找不到，就是「来源不回声」。

v2 相对 v1 的三处升级（2026-10-02）
------------------------------------
  · **JS 页兜底**：urllib 抓回来的正文过薄（<400 字符，JS 渲染壳的典型形态）
    或 403 拦截时，降级用 cdp_read.py（无头浏览器，可滚动、可穿 Shadow DOM）
    重抓一次——覆盖率不再受「官网是不是静态页」限制。找不到浏览器就如实
    UNREACHABLE，不假装查过。
  · **按身份降噪**：创作者/编辑的体验描述（「出片首选机位」）本来就回不了
    原句——UNSUPPORTED 不再计 WARN，降为 INFO；官方/媒体来源的落空才升级。
    「噪声大的闸门一定会被绕过」是本项目既有教训。死线类不豁免：谁说的都判。
  · **核查面扩到 final_plan**：新增 --final-plan，把方案层 activities 的
    description 按 source_refs 映射到来源 URL 一并回查——路书正文的引用
    （不只是线索卡）从此也在回声面上。amap:// 伪 URL 跳过（归留痕面）。

判定分级（每条 claim）
----------------------
  SUPPORTED     页面里找得到声称的值 / 关键短语
  PARTIAL       部分可寻（一句多个值只找到一部分）
  UNSUPPORTED   页面抓到了，但什么都找不到——来源不回声
  UNREACHABLE   抓不到（403 / 风控 / 超时 / DNS）——不是「没有」，是「看不到」
  SKIP          动态源（地图 / 12306，归 claim_audit 管）、或本轮预算用尽

力度（与 E4 死线哲学对齐，宁可WARN不可错杀）
--------------------------
  · 死线类 claim（票价 / 时刻 / 车程距离信号）且 UNSUPPORTED 且**渲染确认**
    （无头浏览器抓到正文后值仍不在）→ **FAIL**——写进路书就要求 [A] 级实证，
    渲染后的来源页里却找不到，没有第二条路。
  · 无渲染能力（机器无浏览器 / CI 环境）时 JS 壳与真缺失不可区分 → 降 WARN：
    「看不到」不判死，这是设计而不是缺陷（东莞实测：官网详情页是 JS 壳，
    本地渲染后值其实在——CI 上若判 FAIL 就是冤案）。
  · 官方/媒体来源的 UNSUPPORTED / PARTIAL → WARN；创作者类 → INFO。
  · UNREACHABLE → WARN + 重查出路，不阻断。
    误报源是真实的：页面在核实日期之后合法改版、动态价格浮动、反爬拦截。
    机器分不清「抄错」和「后来变了」，所以只对钉得住的那一半判死。

不查（边界，写在这里是为了不被读成保证）
--------------------------------------
· 页面是 JS 渲染、且无头浏览器也拿不到的 → UNREACHABLE，不判死
· 页面语义改写（同一事实换个说法）——短语匹配兜不住，归人工
· 抓取只读、单次、每域限量，不做增量监控——变更监控归 bulletin
· 「抓到了且匹配」不保证页面对——引用页本身错了，机器无从知道

用法
----
    python tools/echo_audit.py --clues 社媒线索卡_X.json [--facts 路书_X.json]
                               [--final-plan final_plan_X.json]
                               [--cache 缓存目录] [--timeout 15] [--json]
退出码：0 = 无 FAIL ｜ 2 = 有 FAIL ｜ 1 = 用法/读取错误
离线（无网络）= SKIP 全部并退出 0，但明确打印「离线跳过」，不假装查过。
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

_TOOLS = Path(__file__).resolve().parent

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
ORAL_TIME_PATTERN = (
    r'(?<![零〇一二两三四五六七八九十\d])'
    r'(?P<period>凌晨|早上|上午|中午|下午|晚上)?\s*'
    r'(?P<hour>[零〇一二两三四五六七八九十\d]{1,3})\s*点\s*'
    r'(?P<minute>半|[零〇一二两三四五六七八九十\d]{1,3})\s*(?:分)?'
    r'(?![零〇一二两三四五六七八九十\d])'
)
ORAL_TIME_RE = re.compile(ORAL_TIME_PATTERN)
HOURS_SIG = re.compile(
    r'\d{1,2}[:：]\d{2}\s*[-–~—至]\s*\d{1,2}[:：]\d{2}'
    r'|\d{1,2}[:：]\d{2}'
    r'|' + ORAL_TIME_PATTERN)
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
    r'([零〇一二两三四五六七八九十百千]+)(?:余|多)?\s*(万元|元|公里|千米|米|分钟|小时)')
YI_NUM_RE = re.compile(
    r'(?<![零〇一二两三四五六七八九十百千万亿\d.])'
    r'((?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+)\s*亿'
    r'(?:\s*[零〇一二两三四五六七八九十百千万]+)?)'
    r'\s*(人次|人|元|公里|千米|米|分钟|小时)?')
#: 「900 万人次」「1.5 万」：万级乘法单独抽，抽到后 Arab/中文两种写法都要备变体。
WAN_NUM_RE = re.compile(
    r'(?<![零〇一二两三四五六七八九十百千万亿\d.])'
    r'((?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千]+))\s*万\s*'
    r'(人次|人|元|公里|千米|米|分钟|小时)?')
CN_DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
             '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def cn_to_int(s: str):
    """中文数字 → int（支持到亿：四十二 / 两百零五 / 九百万 / 一亿二千万）。不合法返回 None。"""
    if not s:
        return None
    s = s.strip()
    while len(s) > 1 and s.startswith('零'):
        s = s[1:]
    if '亿' in s:
        head, _, tail = s.partition('亿')
        h = cn_to_int(head)
        if h is None:
            return None
        rest = cn_to_int(tail) if tail else 0
        return h * 100000000 + (rest or 0)
    if '万' in s:
        head, _, tail = s.partition('万')
        h = cn_to_int(head)
        if h is None:
            return None
        rest = cn_to_int(tail) if tail else 0
        return h * 10000 + (rest or 0)
    if '千' in s:
        head, _, tail = s.partition('千')
        h = CN_DIGITS.get(head, 1) if head else 1
        if h is None:
            return None
        rest = cn_to_int(tail) if tail else 0
        return h * 1000 + (rest or 0)
    if '百' in s:
        head, _, tail = s.partition('百')
        h = CN_DIGITS.get(head, 1) if head else 1
        if h is None:
            return None
        rest = cn_to_int(tail) if tail else 0
        return h * 100 + (rest or 0)
    if '十' in s:
        head, _, tail = s.partition('十')
        t = CN_DIGITS.get(head, 1) if head else 1
        if t is None:
            return None
        return t * 10 + (CN_DIGITS.get(tail, 0) if tail else 0)
    if len(s) > 1 and all(ch in CN_DIGITS for ch in s):
        value = 0
        for ch in s:
            value = value * 10 + CN_DIGITS[ch]
        return value
    return CN_DIGITS.get(s)


def int_to_cn(n: int) -> str:
    """int → 中文数字（支持到亿及以上），供页面侧变体匹配。"""
    digits = '零一二三四五六七八九'
    if n >= 100000000:
        yi, rest = divmod(n, 100000000)
        out = int_to_cn(yi) + '亿'
        if not rest:
            return out
        if rest < 10000000:
            return out + '零' + int_to_cn(rest)
        return out + int_to_cn(rest)
    if n >= 10000:
        w, rest = divmod(n, 10000)
        return int_to_cn(w) + '万' + (int_to_cn(rest) if rest else '')
    if n >= 1000:
        h, rest = divmod(n, 1000)
        out = digits[h] + '千'
        if rest == 0:
            return out
        if rest < 100:
            return out + '零' + int_to_cn(rest)
        return out + int_to_cn(rest)
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

#: 数字无单位的关键句兜底：短语回声所需的最长公共片段长度。
ECHO_STRONG = 6     # ≥6 字连续相同 → SUPPORTED（官方页面几乎总会原句收录）
ECHO_WEAK = 3       # ≥3 字 → PARTIAL

#: urllib 正文薄于此值视为 JS 渲染壳 → 触发无头浏览器兜底
THIN_TEXT = 400

MAX_FETCH = 12      # 单轮抓取预算：礼貌上限，用尽的 claim 记 SKIP
FETCH_GAP_SEC = 0.8
FALLBACK_MAX = 3    # 无头浏览器兜底次数上限（每次起浏览器都很重）

#: 身份分档：官方/媒体的落空升级 WARN，创作者/编辑的体验句降 INFO。
OFFICIAL_VOICES = ('OFFICIAL', 'MEDIA')
#: 死线类不豁免——谁说的都判。
ALL_VOICES = ('OFFICIAL', 'MEDIA', 'EDITOR', 'CREATOR', 'TRAVELER', 'LOCAL',
              'COMMENT', 'UNKNOWN')

#: 判定优劣序（多源取最优、渲染重查是否采纳，都以它为准）。
ECHO_RANK = {'SUPPORTED': 3, 'PARTIAL': 2, 'UNSUPPORTED': 1, 'UNREACHABLE': 0}

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


def _variants_for(head: str, scale: int = 1) -> set:
    """claim 数值的页面写法变体集：阿拉伯 / 中文 / 万级全值与紧凑头。

    「60 元」的页面可能写「六十元」；「900 万」的页面可能写「900万 /
    九百万 / 9000000」——只认一种写法会把「写法差异」误判成「不回声」。
    """
    head = unicodedata.normalize('NFKC', head).strip()
    if re.match(r'^\d+(?:\.\d+)?$', head):
        n = float(head)
        variants = {norm_num(head)}
        if n.is_integer() and n >= 1:
            variants.add(int_to_cn(int(n)))
    else:
        n = cn_to_int(head)
        if n is None:
            return {head}
        variants = {head.replace(' ', ''), str(n), int_to_cn(n)}
    if scale != 1:
        full = float(n) * scale
        variants.add(norm_num(str(full)))
        if full.is_integer() and full >= 1:
            variants.add(int_to_cn(int(full)))
    return {v for v in variants if v}


def _yi_number_value(head: str):
    head = re.sub(r'\s+', '', unicodedata.normalize('NFKC', head))
    coefficient, _, tail = head.partition('亿')
    if re.fullmatch(r'\d+(?:\.\d+)?', coefficient):
        value = float(coefficient) * 100000000
    else:
        coefficient_value = cn_to_int(coefficient)
        if coefficient_value is None:
            return None
        value = coefficient_value * 100000000
    tail_value = cn_to_int(tail) if tail else 0
    if tail and tail_value is None:
        return None
    value += tail_value or 0
    return int(value) if float(value).is_integer() else value


def _yi_variants(head: str) -> set:
    normalized = re.sub(r'\s+', '', unicodedata.normalize('NFKC', head))
    value = _yi_number_value(normalized)
    if value is None:
        return {normalized}
    variants = {normalized, norm_num(str(value))}
    compact_yi = norm_num(str(float(value) / 100000000))
    variants.update((compact_yi + '亿', compact_yi + ' 亿'))
    if float(value).is_integer() and value >= 1:
        variants.add(int_to_cn(int(value)))
    return {v for v in variants if v}


def _time_number(value: str):
    value = unicodedata.normalize('NFKC', value)
    if value.isdigit():
        return int(value)
    return cn_to_int(value)


def _time_variants(hour: int, minute: int) -> set:
    return {f'{hour:02d}:{minute:02d}', f'{hour}:{minute:02d}'}


def _oral_time_value(match):
    hour = _time_number(match.group('hour'))
    minute_token = match.group('minute')
    minute = 30 if minute_token == '半' else _time_number(minute_token)
    if hour is None or minute is None or not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    period = match.group('period')
    if period in ('下午', '晚上', '中午') and 1 <= hour <= 11:
        hour += 12
    elif period in ('凌晨', '早上', '上午') and hour == 12:
        hour = 0
    return hour, minute


def claim_values(text: str) -> list:
    """claim 里的可核查值：[(variants 集合, 展示形, 单位), …]。

    亿 / 万级（「1.2 亿」「900 万人次」）与时刻（HH:MM、口语时刻）、
    中文数字都抽。
    """
    vals = []
    for m in YI_NUM_RE.finditer(text):
        head = m.group(1)
        unit = m.group(2) or ''
        vals.append((_yi_variants(head), head.replace(' ', '') + unit, unit or '亿'))
    for m in NUM_UNIT_RE.finditer(text):
        vals.append((_variants_for(m.group(1)), m.group(1) + m.group(2),
                     m.group(2)))
    for m in WAN_NUM_RE.finditer(text):
        head = m.group(1)
        unit = m.group(2) or ''
        variants = _variants_for(head, scale=10000)
        vals.append((variants, head + '万' + unit, unit or '万'))
    for m in CN_NUM_UNIT_RE.finditer(text):
        vals.append((_variants_for(m.group(1)), m.group(1) + m.group(2),
                     m.group(2)))
    for m in TIME_RE.finditer(text):
        t = unicodedata.normalize('NFKC', m.group(0)).replace('：', ':')
        try:
            hour, minute = (int(part) for part in t.split(':'))
        except ValueError:
            vals.append(({t}, t, '时刻'))
            continue
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            variants = _time_variants(hour, minute)
        else:
            variants = {t}
        vals.append((variants, t, '时刻'))
    for m in ORAL_TIME_RE.finditer(text):
        parsed = _oral_time_value(m)
        if parsed is None:
            continue
        hour, minute = parsed
        variants = _time_variants(hour, minute)
        spoken = re.sub(r'\s+', '', unicodedata.normalize('NFKC', m.group(0)))
        variants.add(spoken)
        vals.append((variants, spoken, '时刻'))
    return vals


def is_deadline(text: str) -> bool:
    """死线类判定——口径对齐 source_audit E4：票价/时刻/车程/余票。"""
    if RAIL_RE.search(text) and ('余票' in text or '席别' in text
                                 or '车次' in text):
        return True
    cn_price = (CN_NUM_UNIT_RE.search(text) or WAN_NUM_RE.search(text)
                or YI_NUM_RE.search(text)) and '元' in text
    if DEADLINE_HINT.search(text) and (PRICE_SIG.search(text)
                                       or HOURS_SIG.search(text) or cn_price):
        return True
    if ROUTE_RE.search(text):
        return True
    return False

# ------------------------------------------------------------ 抓取

def _load_cdp():
    """按路径加载 cdp_read 模块（工具间互调的既有惯例），拿 find_browser。"""
    try:
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location('cdp_read_echo',
                                            _TOOLS / 'cdp_read.py')
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


class Fetcher:
    """极简只读抓取器：限量、限速、落缓存（供 cross_check 复用）+ JS 页兜底。"""

    def __init__(self, cache_dir: Path = None, timeout: int = 15,
                 budget: int = MAX_FETCH, gap: float = FETCH_GAP_SEC,
                 use_browser: bool = True):
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.left = budget
        self.gap = gap
        self.offline = None      # None=未测；True=判定离线
        self.use_browser = use_browser
        self.fallback_left = FALLBACK_MAX
        self._cdp = _load_cdp() if use_browser else None
        self._browser = None
        if self._cdp is not None:
            try:
                self._browser = self._cdp.find_browser(None)
            except Exception:
                self._browser = None

    def _cache_path(self, url: str) -> Path:
        h = hashlib.sha256(url.encode('utf-8')).hexdigest()[:20]
        return (self.cache_dir / (h + '.json')) if self.cache_dir else None

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

    def _browser_fetch(self, url: str) -> dict:
        """无头浏览器兜底：JS 壳页 / 403 拦截时的第二跳。找到才算数。"""
        if not (self.use_browser and self._cdp and self._browser):
            return {'status': 'UNREACHABLE', 'text': '',
                    'note': '正文过薄或被拦，且无浏览器可兜底'}
        if self.fallback_left <= 0:
            return {'status': 'UNREACHABLE', 'text': '',
                    'note': '浏览器兜底次数用尽'}
        self.fallback_left -= 1
        try:
            import os
            import tempfile
            out = Path(tempfile.mkdtemp(prefix='echo-cdp-')) / 'page.json'
            # Windows 管道下子进程 stdout 可能落到 ascii/gbk——cdp_read 的
            # --json 含中文与 ❌ 符号，必须强制 UTF-8，否则兜底死于编码而非网络
            env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
            # ⚠️ 不能同时给 --json 和 --out：cdp_read 的 --json 分支先返回，
            # --out 文件根本不会写（v2 首日实测踩到，兜底全数假失败）
            res = None
            for attempt in (1, 2):        # 冷启动抖动重试一次（CI 实测踩到）
                proc = subprocess.run(
                    [sys.executable, str(_TOOLS / 'cdp_read.py'), url,
                     '--out', str(out), '--wait', '4000'],
                    capture_output=True, timeout=90, env=env)
                if proc.returncode == 0 and out.is_file():
                    res = None
                    break
                res = {'status': 'UNREACHABLE', 'text': '',
                       'note': '浏览器兜底读取失败%s'
                               % ('（重试仍败）' if attempt == 2 else '，重试一次')}
            if res:
                return res
            data = json.loads(out.read_text(encoding='utf-8'))
            text = (str(data.get('lightDomText') or '') + ' ' +
                    ' '.join(str(t) for t in (data.get('shadowTexts') or [])))
            text = re.sub(r'\s+', ' ', text).strip()
            if not text:
                return {'status': 'UNREACHABLE', 'text': '',
                        'note': '浏览器渲染后仍无正文'}
            if any(m in text for m in RISK_MARKS):
                return {'status': 'RISK', 'text': '',
                        'note': '兜底渲染命中风控特征'}
            return {'status': 'OK', 'text': text,
                    'note': 'CDP 兜底渲染（urllib 首跳过薄/被拦）'}
        except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
            return {'status': 'UNREACHABLE', 'text': '',
                    'note': '浏览器兜底异常：%s' % str(exc)[:80]}

    def save(self, url: str, res: dict) -> None:
        """把（兜底渲染等）结果写回缓存——cross_check 拿到的就是最终正文。"""
        self._save(self._cache_path(url), url, res)

    def fetch(self, url: str) -> dict:
        """返回 {status: OK/UNREACHABLE/RISK/OFFLINE/BUDGET, text, note}。

        缓存命中直接回；thin/403 触发浏览器兜底，兜底结果同样落缓存
        （cross_check 拿到的就是兜底后的正文）。
        """
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
        # URL 含中文（如 baike.baidu.com/item/松湖烟雨/…）时 http.client 会
        # 以 ascii 编码请求行直接炸——先把非 ASCII 字符百分号编码。
        url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%~")
        req = urllib.request.Request(url, headers={
            'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9'})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                ctype = str(resp.headers.get('Content-Type') or '')
                raw = resp.read(2_000_000).decode('utf-8', 'replace')
        except (urllib.error.URLError, TimeoutError, OSError,
                ValueError) as exc:
            note = str(exc)[:120]
            res = {'status': 'UNREACHABLE', 'text': '', 'note': note}
            if self._should_retry_with_browser(note):
                res = self._browser_fetch(url)
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
        if len(text) < THIN_TEXT:
            # JS 渲染壳的典型形态：HTML 在、正文没有——交给浏览器兜底
            res = self._browser_fetch(url)
            self._save(cp, url, res)
            return res
        res = {'status': 'OK', 'text': text, 'note': ''}
        self._save(cp, url, res)
        return res

    @staticmethod
    def _should_retry_with_browser(note: str) -> bool:
        """403/Forbidden 是「指纹被识破」——真实浏览器指纹可能过得去；
        DNS/超时不值得再起浏览器。"""
        return '403' in note or 'Forbidden' in note


# ------------------------------------------------------------ 核查

def _num_in_page(variants, page: str) -> bool:
    """任一变体在页中即算：两侧不得紧跟数字或小数点——防「16」撞上「2016」。

    中文数字页面（「四十二分钟」）与阿拉伯页面（「42分钟」）互为变体，
    万级另有紧凑形（「900 万」）——只认一种写法会把「写法差异」误判成
    「来源不回声」。时刻冒号不设边界（'16' 撞 '16:00' 属可接受放宽，
    页面几乎总把时刻连上下文原句收录，短语回声再兜一层）。
    """
    for v in variants:
        if re.search(r'(?<![\d.])' + re.escape(v) + r'(?![\d.])', page):
            return True
    return False


def check_claim(claim_text: str, page_text: str) -> dict:
    """对单条 claim 与页面正文做回声比对，返回 {verdict, detail}。"""
    vals = claim_values(claim_text)
    if vals:
        hits, miss = [], []
        for variants, display, _unit in vals:
            if _num_in_page(variants, page_text):
                hits.append(display)
            else:
                miss.append(display)
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
    """从线索卡抽 (claim 文本, url, 核实日期, 身份)。url 缺失的不在核查面。"""
    out, seen = [], set()
    for note in clues.get('notes') or []:
        if not isinstance(note, dict):
            continue
        url = str(note.get('url') or '').strip()
        if not url:
            continue
        voice = str(note.get('voice') or 'UNKNOWN').upper()
        texts = [str(c.get('text') or '') for c in (note.get('claims') or [])
                 if isinstance(c, dict)]
        for ev in (note.get('place_evidence') or []):
            if isinstance(ev, dict):
                texts.append(str(ev.get('text') or ''))
        for t in texts:
            t = t.strip()
            # 同一 URL 下「claims 与 place_evidence 互相重复」是常态——
            # 同一条 claim 抓两次、报两遍，只会把计数搅浑。
            key = (t, url)
            if t and key not in seen:
                seen.add(key)
                out.append({'text': t, 'url': url, 'voice': voice,
                            'checked_at': str(note.get('checked_at') or ''),
                            'title': str(note.get('title') or '')})
    return out


def collect_plan_claims(plan_doc: dict) -> list:
    """方案层回查面：activities 的 description 按 source_refs 映射到来源 URL。

    路书正文里那些「引官网的 [A] 句」多数源自方案层——只查线索卡等于
    核查面漏了正文的出处。amap:// 伪 URL 跳过（数值归 claim_audit 留痕面）。
    """
    out, seen = [], set()
    sources = {str(s.get('id') or ''): s for s in (plan_doc.get('sources') or [])
               if isinstance(s, dict)}
    for day in (plan_doc.get('days') or []):
        for act in (day.get('activities') or []):
            if not isinstance(act, dict):
                continue
            desc = str(act.get('description') or '').strip()
            if not desc:
                continue
            for ref in (act.get('source_refs') or []):
                s = sources.get(str(ref))
                if not s:
                    continue
                url = str(s.get('url') or '').strip()
                if url.startswith('amap://'):
                    continue          # 高德伪协议归 claim_audit 留痕面；
                                      # file:// 允许（测试夹具与本地页合法）
                key = (desc, url)
                if key not in seen:
                    seen.add(key)
                    out.append({'text': desc, 'url': url, 'voice': 'OFFICIAL',
                                'checked_at': str(s.get('checked_at') or '')[:10],
                                'title': str(act.get('name') or '')})
    return out


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='来源回声核查：抓取引用页，验证声称的值真的在页面上（第2层）')
    ap.add_argument('--clues', required=True, help='社媒线索卡 JSON（claims 带 URL）')
    ap.add_argument('--facts', help='事实源 JSON（当前仅用于报告语境，不参与判定）')
    ap.add_argument('--final-plan', dest='final_plan',
                    help='final_plan JSON（方案层 description×source_refs 进核查面）')
    ap.add_argument('--cache', help='抓取缓存目录（供 cross_check 复用；建议给临时目录）')
    ap.add_argument('--timeout', type=int, default=15, help='单页抓取超时秒数')
    ap.add_argument('--budget', type=int, default=MAX_FETCH,
                    help='单轮抓取页数上限（礼貌预算）')
    ap.add_argument('--no-browser', action='store_true',
                    help='禁用无头浏览器兜底（测试用；默认过薄/403 页会起浏览器重抓）')
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
    if a.final_plan:
        plan_path = Path(a.final_plan)
        if not plan_path.is_file():
            emit('final_plan 不存在：%s' % plan_path)
            return 1
        try:
            plan_doc = json.loads(plan_path.read_text(encoding='utf-8'))
        except ValueError as exc:
            emit('final_plan 解析失败：%s' % exc)
            return 1
        claims = claims + collect_plan_claims(plan_doc)
    if not claims:
        emit('线索卡与方案层都没有带 URL 的 claim——回声核查无事可做'
             '（这本身值得警惕：所有说法都给不出来源页）')
        return 0

    cache_dir = Path(a.cache) if a.cache else None
    fetcher = Fetcher(cache_dir=cache_dir, timeout=a.timeout, budget=a.budget,
                      use_browser=not a.no_browser)

    rows = []
    for c in claims:
        dom = domain_of(c['url'])
        row = {'claim': c['text'][:80], 'text': c['text'], 'url': c['url'],
               'domain': dom, 'voice': c['voice'], 'checked_at': c['checked_at']}
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
                note = page.get('note') or ''
                render_confirmed = False
                # —— 官方来源落空 → 浏览器渲染重查一次：静态壳页（JS 渲染站）
                # 的典型形态是「HTML 在、正文是壳」，值只活在渲染后 DOM 里。
                # 只对官方/媒体来源重查（创作者体验句本就不回声，别烧预算）。
                if (res['verdict'] in ('UNSUPPORTED', 'PARTIAL')
                        and c['voice'] in OFFICIAL_VOICES):
                    page2 = fetcher._browser_fetch(c['url'])
                    if page2['status'] == 'OK':
                        render_confirmed = True
                        res2 = check_claim(c['text'], page2['text'])
                        if ECHO_RANK[res2['verdict']] > ECHO_RANK[res['verdict']]:
                            res, note = res2, page2.get('note') or ''
                        fetcher.save(c['url'], page2)
                row.update(verdict=res['verdict'],
                           render_confirmed=render_confirmed,
                           detail=res['detail'] + ('｜' + note if note else ''))
        rows.append(row)

    # —— 按 claim 文本聚合取最优：同一句引了两个源，一个回声、一个没提，
    # 引用仍然成立（第二个源本来就不是为这句引的）；**全部落空**才是编造引用。
    # 东莞实测教训：官网 ✓ + 本地生活站 ✗ 的组合曾被逐源各判，误伤成 FAIL。
    groups, order = {}, []
    for r in rows:
        key = r['text']
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    grouped = []
    n_fail = n_warn = n_info = n_downgraded = 0
    # CI 环境（GitHub Actions 等）里外部网络不可控：渲染结果随出口 IP /
    # 加载时序抖动，FAIL 会假红。诚实降级——FAIL 转 WARN 并明示「本机复核为准」。
    ci_env = bool(os.environ.get('GITHUB_ACTIONS'))
    for key in order:
        rs = groups[key]
        checked = [r for r in rs if r['verdict'] in ECHO_RANK]
        if not checked:
            effective = 'SKIP'
            detail = rs[0]['detail']
        else:
            best = max(checked, key=lambda r: ECHO_RANK[r['verdict']])
            effective = best['verdict']
            detail = best['detail']
            others = [r for r in checked if r is not best]
            if others and effective == 'SUPPORTED':
                miss = sum(1 for r in others
                           if r['verdict'] in ('UNSUPPORTED', 'UNREACHABLE'))
                if miss:
                    detail += '（另 %d 个被引源未回声——引用仍成立，但值得知道）' % miss
        text = rs[0]['text']
        # FAIL 必须渲染确认：浏览器抓到正文后值仍不在，才判「编造引用」。
        # 无渲染能力（无浏览器 / CI）时 JS 壳与真缺失不可区分 → 降 WARN，
        # 不冤枉（东莞实测：官网详情页是 JS 壳，本地渲染后值其实在）。
        if effective == 'UNSUPPORTED' and is_deadline(text) \
                and any(r.get('render_confirmed') for r in rs):
            if ci_env:
                n_warn += 1
                n_downgraded += 1
                detail = ('CI 环境降级（外部网络不可控，本机复核为准）：' + detail)
            else:
                n_fail += 1
        elif effective in ('UNSUPPORTED', 'PARTIAL') \
                and rs[0]['voice'] not in OFFICIAL_VOICES:
            n_info += 1
        elif effective in ('UNSUPPORTED', 'PARTIAL', 'UNREACHABLE'):
            n_warn += 1
        grouped.append({'claim': key[:80], 'text': key,
                        'voice': rs[0]['voice'],
                        'checked_at': rs[0]['checked_at'],
                        'verdict': effective, 'detail': detail,
                        'sources': [{'domain': r['domain'], 'url': r['url'],
                                     'verdict': r['verdict']} for r in rs]})

    result = {
        'clues': str(clues_path),
        'final_plan': str(a.final_plan) if a.final_plan else None,
        'claims_total': len(claims),
        'claims_unique': len(grouped),
        'claims': grouped,
        'fail': n_fail,
        'warn': n_warn,
        'info': n_info,
        'ok': n_fail == 0,
        'scope': ('v2 只核查 claim 的数值与关键短语是否在引用页正文中回声'
                  '（同一 claim 多源取最优——任一被引源回声即算引用成立）；'
                  '语义改写、无头浏览器也拿不到的页面、页面本身的内容错误不在能力内。'),
        'not_checked': ('页面语义等价改写；页面在核实日期之后的变化是否合法；'
                        '引用页自身内容是否正确（链条止于「页面怎么说」）。'),
    }

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0 if result['ok'] else 2

    emit('=' * 74)
    emit('来源回声核查（echo_audit v2）｜ 线索卡：%s%s ｜ claim %d 条（去重 %d）'
         % (clues_path.name,
            ' + final_plan' if a.final_plan else '', len(claims), len(grouped)))
    emit('=' * 74)
    marks = {'SUPPORTED': '[OK]', 'PARTIAL': '[~ ]', 'UNSUPPORTED': '[X ]',
             'UNREACHABLE': '[? ]', 'SKIP': '[--]'}
    for r in grouped:
        doms = '、'.join(s['domain'] for s in r['sources'][:2])
        emit('  %s %-12s %-8s %s' % (marks[r['verdict']], r['verdict'],
                                     r['voice'][:8], r['claim'][:52]))
        emit('       %s ｜ %s' % (doms, r['detail'][:72]))
    emit('-' * 74)
    for v in ('SUPPORTED', 'PARTIAL', 'UNSUPPORTED', 'UNREACHABLE', 'SKIP'):
        n = sum(1 for r in grouped if r['verdict'] == v)
        if n:
            emit('  %-12s %d' % (v, n))
    if n_fail:
        emit('[X] FAIL %d 处——死线类声明在引用页里找不到值，先重查或改写再交付'
             % n_fail)
        emit('    误报说明：若页面在核实日期之后合法改版，更新核查日期并重查。')
        return 2
    emit('[OK] 回声核查无 FAIL（WARN %d / INFO %d——点名清单，不阻断）'
         % (n_warn, n_info))
    if n_downgraded:
        emit('    ⚠ CI 环境：%d 处本可 FAIL 的死线落空已降级为 WARN'
             '——请在本机重跑复核后再交付。' % n_downgraded)
    emit('    INFO＝创作者体验句不回声，属预期形态；WARN＝官方来源落空，值得看一眼。')
    emit('    ⚠️ 「页面里有」≠「页面说的对」——本闸门只证明回声，不证明真理。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
