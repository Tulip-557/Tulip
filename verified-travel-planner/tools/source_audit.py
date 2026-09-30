#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
source_audit.py — 源核验：证据徽章的「标注纪律」闸门

为什么需要它
------------
铁律 1 说「每个数字都有来源」——但**标注是声明，核验才是证据**。
`[A]` 写上去只要一个方括号，可它声称的事情（有来源、有时戳、可追溯）
没有任何机器去验证过。freshness.py 查的是「新不新」（时戳维度），
本脚本查的是「配不配」（纪律维度）：

  规则                查什么                                    违反后果
  ─────────────────  ───────────────────────────────────────  ────────
  E1 徽章在册          全文 A/B/C/D 徽章总数 > 0                 FAIL
  E2 [A] 时戳来源      徽章句有「日期 + 来源词」，slot 内继承      PASS/WARN/FAIL
                      只算 WARN，连 slot 都没有就 FAIL
  E3 [B] 双源          徽章句 ±1 句窗口内 ≥2 个不同来源词         PASS/WARN/FAIL
                      （「高德 POI + 店铺信息」这种第二源不明算 WARN）
  E4 [C] 死线          C 徽章句含价格/车程/时刻/开放时段 ——         FAIL
                      evidence-rules.md 明令 C 级不得用于这几类；
                      「两说并存」类多源矛盾并列是合法写法，降为 WARN
  E5 [D] 出路          徽章句 + 下句有「用户可自行确认的方式」      PASS/WARN
                      （决策注记类 [D] 给不出确认方式，只 WARN 不 FAIL）
  E6 source 闭环       final_plan 的 source_refs 全部可解析、       FAIL/WARN
                      sources 无孤儿条目（需 --plan）
  E7 同名数字一致      同一 POI 多处「高德评分」不同值 → FAIL；
                      「人均」区间无交集 → WARN
  E8 书写规范          小写徽章 / [C] 缺「·单源」标注              WARN

用法
----
    python source_audit.py --facts <事实源.json>
    python source_audit.py --facts <事实源.json> --plan <final_plan.json>
    python source_audit.py --facts <事实源.json> --json        # 机读输出

退出码：0 = 无 FAIL ｜ 2 = 有 FAIL（与 evaluate 一致：排不通不交付）
       WARN 不改退出码——它是「该补课」的清单，不是「排不通」的判决。

## 它不查什么（能力边界，别读成保证）

本脚本是**标注纪律**的静态审计。它查「标得配不配」，**不查「标的是不是真的」**：

- ❌ 一句话标了 `[A]`，**它是否真出现在所引来源里**——不查。
  它只确认「有日期 + 有来源词」，无法确认那个来源里真有这条信息。
- ❌ 数字本身对不对（票价是不是 60 元、车程是不是 25 分钟）——不查。
- ❌ 来源本身可不可信——不查（社区内容标 `[C·单源]` 即合规，不代表内容可靠）。

**为什么会这样**：事实源只存结论、不存原始返回，所以没有任何东西可供比对。
要补齐这一层必须**在生成阶段留存快照**（高德返回体、网页正文摘录），再做
「声明 ↔ 快照」比对——见 `references/source-audit.md` 的留痕路线与
`README.md` 待办 P1。

> **把它读成什么**：这套闸门保证的是「**你说得出这个数字是哪来的**」，
> 不是「**这个数字一定对**」。交付物里的「未核实项清单」才是后者交给用户的部分。
> 两者别混——混了就会出现「过了闸门 = 内容已核实」这种致命误解。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter

# ------------------------------------------------------------ 常量

# 规则清单（validate_skill.py 的「源核验规则数」断言以本表长度为事实值；
# 加规则只改这里一处 —— 文档里的「N 条规则」会随之校验）
RULES = [
    ('E1', '徽章在册',       '全文 A/B/C/D 徽章总数 > 0'),
    ('E2', '[A] 时戳来源',   '徽章句有「日期 + 来源词」，slot 级继承算 WARN'),
    ('E3', '[B] 双源',       '徽章句 ±1 句窗口内 ≥2 个不同来源词'),
    ('E4', '死线',           '门票价/车程/时刻/开放时段不得落在 [C]；[B] 记 WARN'),
    ('E5', '[D] 出路',       '徽章句 + 下句给出「怎么自行确认」'),
    ('E6', 'source 闭环',    'source_refs 与 sources 双向对得上'),
    ('E7', '同名数字一致',   '同一 POI 的评分/人均多处声明不打架'),
    ('E8', '书写规范',       '[C] 带「·单源」标注'),
]

# 来源词表 —— 与 tools/freshness.py 的来源词保持同步（改动须两头改）。
# 2026-09-27 源核验实测补充：'文旅'/'协会'/'食评'/'住客笔记' 不在词表时，
# 「广东省文旅发布…[B]」会被误判成无来源——词表覆盖度直接决定 E2/E3 的误报率。
SOURCE_WORDS = (
    '高德', '点评', '携程', '同程', '12306', '官网', '官方', '公众号',
    '小红书', '抖音', 'B站', '马蜂窝', '南方日报', '中山日报', '腾讯新闻',
    '美团', '飞猪', '去哪儿', 'OTA', '政务网', '政府门户', '日报', '新闻',
    '铁路', '景区', '博物馆', '文旅', '协会', '食评', '住客笔记',
)
SRC_RE = re.compile('|'.join(SOURCE_WORDS))
DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')
BADGE_RE = re.compile(r'\[([ABCD])([^\]]*)\]')
# 死线禁区：注意**语境收窄** —— 探针实测教训：
#   「人均 ¥40」是餐费/预算参考，不在 evidence-rules 死线清单里；
#   「每 5–15 分钟一班」是班次密度，不是换乘车程；
#   「多云 27–33℃」整段被表格拼接后会被裸「车程」两字误伤。
#   所以价格要带票务语境、车程要带交通方式词或公里配对，班次密度单独 WARN。
TICKET_CTX = '门票|票价|票：|成人票|双人票|夜票|套票|入园|门票价'
ROUTE_RE = re.compile(
    r'\d+(?:\.\d+)?\s*公里'                                   # 距离
    r'|\d+\s*分钟\s*/\s*\d'                                   # 「X 分钟 / Y 公里」配对
    r'|(?:打车|车程|步行|驾车|骑行|公交)\D{0,8}\d+\s*分钟'      # 交通方式 + 分钟
)
DENSITY_RE = re.compile(r'每\s*\d+(?:[–~\-—]\d+)?\s*分钟一班')
# 多源矛盾并列的合法标记（evidence-rules.md「多源矛盾的处理」认可这种写法）
CONFLICT_MARK = re.compile(r'两说|两种说法|并存|矛盾|口径')
# 景观长度词 —— 「12 公里环湖绿道」是景观描述不是换乘距离
SCENIC_LEN = re.compile(r'绿道|环湖|海岸线|栈道|徒步道|骑行道')
# 死线信号（供归属模型用）
PRICE_SIG = re.compile(r'[¥￥]\s*\d|\d+\s*元|免费|免票')
HOURS_SIG = re.compile(r'\d{1,2}[:：]\d{2}\s*[-–~—至]\s*\d{1,2}[:：]\d{2}')
# [D] 的「用户可自行确认的方式」
D_OUT_RE = re.compile(
    r'公众号|官网|官方渠道|可核|电话|自查|确认|拨打|12306|[Aa]pp|搜|现场|告示|以.{0,6}为准'
    r'|直接|跳过|改去|省事|改住|砍掉|对比|比价|查一'
)
RATE_RE = re.compile(r'高德(?:评分)?\s*([0-9]\.\d)')
PERCAP_RE = re.compile(r'人均(?:约)?\s*[¥￥]?\s*(\d+)\s*(?:[–~\-—至]\s*(\d+))?')
POI_BOLD_RE = re.compile(r'<b>([^<]{2,30})</b>')

_SYM_FALLBACK = str.maketrans({
    '✅': '[OK]', '✓': '[OK]', '√': '[OK]', '❌': '[X]', '✗': '[X]',
    '×': '[X]', '→': '->', '■': '[*]', '●': '[*]', '·': '.',
    '⚠': '[!]', '｜': '|',
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


def strip_html(s):
    # 块级标签（含表格单元格 </td>）转行 —— cost 表整表无句读，
    # 不拆会把「门票」表头的语境泼到全表每个数字上（实测踩过）
    s = re.sub(r'</(?:p|td|tr|li|h[1-6]|div)>|<br\s*/?>', '\n', s, flags=re.I)
    s = re.sub(r'<[^>]+>', '', s)
    return s


def split_sents(text):
    return [p.strip() for p in re.split(r'(?<=[。；;\n])', text) if p.strip()]


_TRACKS = (('full', '完整版'), ('compact', '精简版'))


def slot_texts(loc, body):
    """把 `days[].slots[].body` 展开成 `[(定位符, 文本), …]`。

    **两档都要审**：完整版读 full、精简版读 compact，只审一档等于放掉另一半——
    而且 compact 档是新写的、压缩过的最容易漏时戳与徽章，正是最该被审的那一档。

    但**不能合并成一个定位符**：那会让同一处在两档各报一条，定位符完全一样、
    正文只差几个字，读者根本分不清是「同一处报了两遍」还是「两处都有问题」。
    2026-09-27 实测：中山一轮 29 条 WARN 里近半数是这种成对重复，
    而「噪声大的闸门一定会被绕过」是本项目的既有教训。

    分开还顺带修正了 E2 的口径：slot 级继承改成**档内继承**——精简版读者只看得见
    compact 档，拿完整版的时戳去「继承」等于替他补了一个他看不到的来源。

    单档（字符串 body）原样返回、不加后缀，保持既有输出与既有基线一字不变。
    """
    if isinstance(body, dict):
        out = []
        for key, label in _TRACKS:
            txt = str(body.get(key) or '')
            if txt.strip():
                out.append(('%s·%s' % (loc, label), txt))
        return out
    return [(loc, str(body or ''))]


# ------------------------------------------------------------ 收集徽章

def collect_badges(facts):
    """递归遍历事实源的全部文本，抽 (位置, 句, 句列表, 序号, 徽章等级, extra)。"""
    out = []
    texts = []   # (loc, text)

    for di, day in enumerate(facts.get('days', [])):
        dname = 'D%d' % (di + 1)
        for slot in day.get('slots', []):
            texts.extend(slot_texts('%s %s' % (dname, slot.get('time', '?')),
                                    slot.get('body')))
        for note in day.get('notes', []):
            texts.append(('%s note' % dname, note.get('text', '')))
    for sid, sec in facts.get('sections', {}).items():
        if isinstance(sec, str):
            texts.append(('sec:%s' % sid, sec))
            continue
        for k, v in sec.items():
            if isinstance(v, str):
                texts.append(('sec:%s.%s' % (sid, k), v))
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    if isinstance(item, str):
                        texts.append(('sec:%s.%s[%d]' % (sid, k, i), item))
                    elif isinstance(item, dict):
                        for k2, v2 in item.items():
                            if isinstance(v2, str):
                                texts.append(('sec:%s.%s[%d].%s' % (sid, k, i, k2), v2))

    for loc, raw in texts:
        sents = split_sents(strip_html(raw))
        for si, sent in enumerate(sents):
            for m in BADGE_RE.finditer(sent):
                out.append(dict(loc=loc, sent=sent, sents=sents, si=si,
                                level=m.group(1), extra=m.group(2)))
    return out, texts


# ------------------------------------------------------------ 规则实现

def rule_e1(badges):
    """徽章在册：0 徽章 = 老规范产物，无源可核。"""
    if not badges:
        return ('FAIL', '全文 0 枚证据徽章——老规范产物或漏标，'
                        'evidence-rules.md 的四级标注一行都没落')
    c = Counter(b['level'] for b in badges)
    return ('PASS', '徽章 %d 枚：A%d / B%d / C%d / D%d'
            % (len(badges), c['A'], c['B'], c['C'], c['D']))


def _win(b, span=1):
    lo, hi = b['si'] - span, b['si'] + span + 1
    return ' '.join(b['sents'][max(0, lo):max(1, hi)])


def rule_e2(badges, texts):
    """[A] 必须带时戳与来源。句内为佳；slot 内继承算 WARN；全无 FAIL。"""
    fails, warns, oks = [], [], 0
    for b in badges:
        if b['level'] != 'A':
            continue
        if DATE_RE.search(b['sent']) and SRC_RE.search(b['sent']):
            oks += 1
            continue
        # slot 级继承：同一 loc 的原文里有「日期+来源词」
        slot_raw = next((t for loc, t in texts if loc == b['loc']), '')
        if DATE_RE.search(slot_raw) and SRC_RE.search(slot_raw):
            warns.append((b, '时戳/来源靠 slot 内继承，徽章句自身没带'))
        else:
            fails.append((b, '句与所在 slot 都没有「日期 + 来源词」'))
    return oks, warns, fails


def rule_e3(badges):
    """[B] 声称多源交叉，句 ±1 内须数得出 ≥2 个不同来源词。"""
    fails, warns, oks = [], [], 0
    for b in badges:
        if b['level'] != 'B':
            continue
        found = {w for w in SOURCE_WORDS if w in _win(b)}
        if len(found) >= 2:
            oks += 1
        elif len(found) == 1:
            warns.append((b, '窗口内只数得出 1 个来源词（%s）——第二源是谁没写' % '/'.join(found)))
        else:
            fails.append((b, '徽章句 ±1 内没有任何来源词'))
    return oks, warns, fails


def _nearest_badge(sent, pos):
    """死线信号归属：句内离 pos 最近的徽章等级（无徽章返回 None）。"""
    best, best_d = None, None
    for m in BADGE_RE.finditer(sent):
        d = min(abs(m.start() - pos), abs(m.end() - pos))
        if best_d is None or d < best_d:
            best, best_d = m.group(1), d
    return best


def rule_e4(badges):
    """死线：门票价/车程/时刻/开放时段绝不能落在 C 徽章上。

    归属模型：一句里可能同时有「车程…[A]」和「档期…[B]」——死线信号
    归给**离它最近的徽章**，[A] 认领的数字不连累同句其他徽章。
    等级分档：
      · 归属 [C] → FAIL（evidence-rules.md 明文死线）
      · 归属 [B] → WARN（SKILL.md 要求这几类取 [A]；第三方聚合双源
        是次优解，点名建议官方渠道复核，不判死刑）
    """
    fails, warns = [], []
    for b in badges:
        if b['level'] not in ('B', 'C'):
            continue
        sent = b['sent']
        ctx = ' '.join(b['sents'][max(0, b['si'] - 1):b['si'] + 2])
        hits = []

        for sig_re, ctx_pat, label, need_ctx in (
                (PRICE_SIG, TICKET_CTX, '门票价格', True),
                (ROUTE_RE, None, '车程/距离', False),
                (HOURS_SIG, r'开放|营业|闭馆|入场|入园|停止', '开放时段', True)):
            ctx_re = re.compile(ctx_pat) if ctx_pat else None
            for m in sig_re.finditer(sent):
                seg = sent[max(0, m.start() - 6):m.end() + 6]
                if label == '车程/距离' and SCENIC_LEN.search(seg):
                    continue                    # 景观长度，不是换乘距离
                if need_ctx and not (
                        ctx_re.search(sent) or '票价' in b['extra']):
                    continue
                owner = _nearest_badge(sent, m.start())
                if owner == b['level']:
                    if label not in hits:
                        hits.append(label)
                    break
        if DENSITY_RE.search(sent):
            warns.append((b, '班次密度落在 [%s] 上——时刻类信息无 [A] 级来源'
                             '（第三方聚合按 SKILL.md 不给 [A]）' % b['level']))
            continue
        if not hits:
            continue
        if CONFLICT_MARK.search(ctx):
            warns.append((b, '%s 落在 [%s] 上——多源矛盾并列是合法写法，'
                           '但终究没有 [A] 级来源' % ('+'.join(hits), b['level'])))
        elif b['level'] == 'C':
            fails.append((b, '%s 落在 [%s] 上——evidence-rules.md 死线：'
                           'C 级绝不能用于这几类' % ('+'.join(hits), b['level'])))
        else:
            warns.append((b, '%s 落在 [B] 上——SKILL.md 要求这类信息取 [A]；'
                           '第三方聚合双源是次优，建议官方渠道复核'
                           % '+'.join(hits)))
    return warns, fails


def rule_e5(badges):
    """[D] 须给出用户可自行确认的方式（或行动出路）。"""
    warns, oks = [], 0
    for b in badges:
        if b['level'] != 'D':
            continue
        win = b['sent']
        if b['si'] + 1 < len(b['sents']):
            win += b['sents'][b['si'] + 1]
        if D_OUT_RE.search(win):
            oks += 1
        else:
            warns.append((b, '句 + 下句里找不到「怎么自行确认」的出路'))
    return oks, warns


def rule_e6(plan_path):
    """final_plan 的 source_refs 闭环 + 孤儿来源。"""
    fails, warns = [], []
    if not plan_path:
        return None, None, None
    try:
        plan = json.load(open(plan_path, encoding='utf-8'))
    except (OSError, ValueError) as e:
        return (['--plan 读取失败：%s' % e], [], None)
    ids = [s.get('id') for s in plan.get('sources', [])]
    known = set(i for i in ids if i)
    refs = []
    for day in plan.get('days', []):
        for act in day.get('activities', []):
            refs.extend(act.get('source_refs', []))
    for r in refs:
        if r not in known:
            fails.append((dict(loc='plan', sent=r, sents=[], si=0, level='?'),
                          'source_ref 指向不存在的来源 id'))
    for i in known:
        if i not in refs:
            warns.append((dict(loc='plan', sent=i, sents=[], si=0, level='?'),
                          'sources 条目从未被引用（孤儿来源）'))
    return (fails, warns, len(refs))


def rule_e7(texts):
    """同一 POI 的数字跨 slot 一致：评分冲突 FAIL；人均区间无交集 WARN。"""
    fails, warns = [], []
    rates, percaps = {}, {}
    for loc, raw in texts:
        for pm in POI_BOLD_RE.finditer(raw):
            name = pm.group(1)
            rm = RATE_RE.search(raw[pm.end():pm.end() + 60])
            if rm:
                rates.setdefault(name, set()).add(rm.group(1))
            cm = PERCAP_RE.search(raw[pm.end():pm.end() + 80])
            if cm:
                percaps.setdefault(name, []).append(
                    (int(cm.group(1)), int(cm.group(2) or cm.group(1))))
    for name, vals in rates.items():
        if len(vals) > 1:
            fails.append((dict(loc='*', sent=name, sents=[], si=0, level='?'),
                          '同一 POI 评分两处不同：%s' % '/'.join(sorted(vals))))
    for name, spans in percaps.items():
        if len(spans) >= 2:
            lo = max(s[0] for s in spans)
            hi = min(s[1] for s in spans)
            if lo > hi:
                warns.append((dict(loc='*', sent=name, sents=[], si=0, level='?'),
                              '人均区间多处声明无交集：%s' % ' vs '.join(
                                  '%d-%d' % s for s in spans)))
    return fails, warns


def rule_e8(badges):
    """书写规范：小写徽章 / [C] 缺「单源」标注。"""
    warns = []
    for b in badges:
        if b['level'] == 'C' and '单源' not in b['extra']:
            warns.append((b, '[C] 没带「·单源」标注——写法见 evidence-rules.md'))
    return warns


# ------------------------------------------------------------ 报告

def excerpt(b, n=64):
    s = b['sent']
    return s if len(s) <= n else s[:n] + '…'


def report_findings(title, items):
    emit('  %s %d 条' % (title, len(items)))
    for b, why in items:
        emit('    [%s] %s' % (b.get('loc', '?'), why))
        emit('        …%s' % excerpt(b))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='源核验：证据徽章标注纪律闸门。'
                    '⚠️ 只审「标注纪律」（标得配不配），**不审内容真实**'
                    '（标了 [A] 的那句是否真在所引来源里）——'
                    '后者需生成阶段留痕快照才能做，见 references/source-audit.md')
    ap.add_argument('--facts', required=True, help='事实源 JSON 路径')
    ap.add_argument('--plan', help='final_plan JSON 路径（做 source 闭环核验）')
    ap.add_argument('--json', action='store_true', help='机读输出')
    ap.add_argument('--lint', action='store_true',
                    help='写作期预检：只列 FAIL 级（WARN 不出，免噪声）。'
                         '跑同一套规则，但**过 lint ≠ 过闸门**——交付前仍须跑完整模式')
    args = ap.parse_args(argv)

    try:
        facts = json.load(open(args.facts, encoding='utf-8'))
    except (OSError, ValueError) as e:
        emit('事实源读取失败：%s' % e)
        return 2

    badges, texts = collect_badges(facts)

    result = {
        'file': args.facts,
        # 能力边界随结果一起返回：机读消费方不该以为「无 FAIL」等于「内容已核实」。
        'scope': '标注纪律（来源/时戳/死线/出路/闭环）',
        'not_checked': '内容真实——标了 [A] 的那句是否真在所引来源里（需生成阶段留痕快照）',
        'rules': {}, 'exit_fail': False}

    # E1（单独处理：0 徽章 = FAIL，且必须计入退出码）
    st, msg = rule_e1(badges)
    result['rules']['E1 徽章在册'] = {
        'status': st,
        'pass': len(badges) if st == 'PASS' else 0,
        'warn': 0, 'fail': 0 if st == 'PASS' else 1,
        'detail': msg}
    if st == 'FAIL':
        result['exit_fail'] = True
    # E2/E3
    a_ok, a_w, a_f = rule_e2(badges, texts)
    b_ok, b_w, b_f = rule_e3(badges)
    # E4/E5/E7/E8
    c_w, c_f = rule_e4(badges)
    d_ok, d_w = rule_e5(badges)
    e7_f, e7_w = rule_e7(texts)
    e8_w = rule_e8(badges)
    # E6
    e6 = rule_e6(args.plan)

    def rule_row(key, oks, warns, fails, skip=False):
        if skip:
            result['rules'][key] = {'status': 'SKIP', 'detail': '未提供 --plan'}
            return
        n_fail, n_warn = len(fails or []), len(warns or [])
        st = 'FAIL' if n_fail else ('WARN' if n_warn else 'PASS')
        result['rules'][key] = {
            'status': st, 'pass': oks, 'warn': n_warn, 'fail': n_fail}
        if st == 'FAIL':
            result['exit_fail'] = True

    rule_row('E2 [A]时戳来源', a_ok, a_w, a_f)
    rule_row('E3 [B]双源', b_ok, b_w, b_f)
    rule_row('E4 [C]死线', 0, c_w, c_f)
    rule_row('E5 [D]出路', d_ok, d_w, [])
    rule_row('E6 source闭环', None, e6[1], e6[0], skip=e6[0] is None and e6[1] is None)
    rule_row('E7 同名数字一致', 0, e7_w, e7_f)
    rule_row('E8 书写规范', 0, e8_w, [])

    if args.lint:
        # 写作期预检：与正式模式跑同一套规则（零语义复制），只做三件事——
        # ① 只列 FAIL（WARN 在写作期只制造噪声）② 紧凑单行 ③ 明示边界。
        # 它的存在理由是东莞轮实测：source_audit 首跑 FAIL 75，
        # 写完才被闸门打回、返工 34 个单元 × 2 轮——写作时有即时反馈能把
        # 返工压到 1 轮内。
        fails = []
        if st == 'FAIL':                       # E1：全文 0 徽章
            fails.append(('E1', '?', msg))
        for key, items in (('E2', a_f), ('E3', b_f), ('E4', c_f), ('E7', e7_f)):
            for b, why in items:
                fails.append((key, b.get('loc', '?'), why))
        for key, val in result['rules'].items():
            if key.startswith('E6') and val['status'] == 'FAIL':
                for b, why in (e6[0] or []):
                    fails.append((key[:2], b.get('loc', '?'), why))
        if fails:
            emit('lint · %s：%d 处 FAIL' % (args.facts, len(fails)))
            for key, loc, why in fails:
                emit('  [%s] %s — %s' % (key, loc, why))
            emit('  过 lint 不等于过闸门：修完这里，交付前仍须跑完整 source_audit（含 WARN 与 E5/E6/E8）')
            return 2
        emit('lint · %s：FAIL 0（写作纪律过关。注意：lint 不查 WARN/E5/E6/E8，交付前仍须跑完整闸门）'
             % args.facts)
        return 0

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 2 if result['exit_fail'] else 0

    emit('=' * 62)
    emit('源核验 · %s' % args.facts)
    emit('=' * 62)
    emit('[E1] %s %s' % (st, msg))
    for key, val in result['rules'].items():
        if val['status'] == 'SKIP':
            emit('[%s] SKIP %s' % (key[:3], val['detail']))
            continue
        mark = {'PASS': 'OK ', 'WARN': '!  ', 'FAIL': 'X  '}[val['status']]
        if 'pass' in val:
            extra = '  过 %s / 警 %d / 败 %d' % (val['pass'], val['warn'], val['fail'])
        else:
            extra = ''
        emit('[%s] %s%s%s' % (key[:3], mark, key, extra))
    emit('-' * 62)
    for title, items in (('FAIL（违反纪律，须改）', a_f + b_f + c_f
                          + (e6[0] or []) + e7_f),
                         ('WARN（该补课，不阻断）', a_w + b_w + c_w
                          + d_w + (e6[1] or []) + e7_w + e8_w)):
        if items:
            report_findings(title, items)
            emit('')
    n_fail = sum(v.get('fail', 0) for v in result['rules'].values()
                 if isinstance(v, dict))
    n_warn = sum(v.get('warn', 0) for v in result['rules'].values()
                 if isinstance(v, dict))
    emit('汇总：FAIL %d ｜ WARN %d —— %s' % (
        n_fail, n_warn,
        '存在违反标注纪律的徽章，先修再交付' if result['exit_fail']
        else '标注纪律过关（WARN 是补课清单）'))
    # 每次跑都提醒一次能力边界。绿了不等于「内容已核实」——
    # 这个误解一旦形成，整套证据分级的价值就反过来了。
    emit('')
    emit('⚠️ 本闸门只审「标注纪律」：源、时戳、死线、出路、闭环是否合规。')
    emit('   它**不查内容真实**——标了 [A] 的那句话是否真在所引来源里，'
         '本脚本无从判断。')
    emit('   要补这一层须在生成阶段留痕快照（高德返回体/网页摘录）再比对；'
         '未完成前，')
    emit('   「过了闸门」只等于「说得出来源」，不等于「数字一定对」。'
         '见 references/source-audit.md。')
    return 2 if result['exit_fail'] else 0


if __name__ == '__main__':
    sys.exit(main())
