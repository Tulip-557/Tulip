#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""精简版核验 —— 精简版（执行件）的机器闸门。

为什么需要它
------------
`--compact` 只保证「取 compact 档」这件事被执行了，**不保证取到的东西是合格的**。
实测踩到过的三个真缺陷，都是这个工具现在要拦的：

1. **压缩时丢掉强制约束**——「17:30 停止入园」「末班 21:00」这类句子最容易在求短时
   被当作"啰嗦"删掉，而它们恰恰是路上最不能丢的；
2. **压缩时丢掉来源时戳**——`[A]` 声明失去可追溯性（中山实测：删掉 tips 段里唯一的
   「查询于 2026-09-27」，两条 `[A]` 立刻从 WARN 升级为 FAIL）；
3. **根本没压**——`sections` 写了紧凑档、`days[].slots` 没写，结果全篇只省 15%
   （days 占约 65%）。"写了 compact 档"是**数得出来**的，不该靠感觉。

判据取向：**宁松勿噪**。阈值卡太紧会让「改一句文案就红灯」，闸门就退化成噪声源，
人第二次就不看了。所以硬 FAIL 只留三类硬伤，比例类给 WARN 并照实打印数字。

用法
----
    python tools/compact_check.py --facts 路书_XX_事实源.json
    python tools/compact_check.py --facts 路书_XX_事实源.json \\
        --full-html 路书_XX.html --compact-html 路书_XX_精简版.html

退出码：0 = 无 FAIL；2 = 有 FAIL（与 evaluate / source_audit 同一纪律）。
零第三方依赖，可进 CI。
"""
import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- 判据常量

#: 强制约束词——这些句子里的数字必须活到精简版。
#:
#: ⚠️ 词表刻意收窄过一次：初版含「提前 / 当天 / 最后一班」，结果把
#: 「今天比常规提前开饭，是为了给夜游留出整段（换乘缓冲 15 分钟之后还要 21 分钟车程）」
#: 这种**排程理由句**也当成了约束句——那是解释，不是约束，删掉不算丢东西。
#: **误报比漏报更伤**：满屏假警报，人第二次就不看了。
CONSTRAINT_RE = re.compile(
    r'末班|最晚|截止|停止入场|停止入园|停止入馆|闭馆|退房|寄存|必须|务必|'
    r'留足|立刻|马上|无人|不可|不要去|不要带|逾期|需提前|只能|'
    r'需预约|要预约|预约截止')

#: 明确声明「本版不展开」的单元——有意留白，不参与证据/精简度比较
OMITTED_MARK = '本版不展开'

#: 约束句里需要跟过去的数字（时间 / 时长 / 价格 / 距离）
NUM_RE = re.compile(
    r'\d{1,2}\s*[:：]\s*\d{2}|\d+(?:\.\d+)?\s*(?:分钟|小时)|'
    r'[¥￥]\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*元|\d+(?:\.\d+)?\s*公里')

#: 证据标记（与 ludbook_check / source_audit 同一口径）
EVIDENCE_RE = re.compile(r'\[[ABCD][^\]]*\]')

#: 逐段精简度：full 超过这么多字，compact 却几乎没短，就值得点名
MIN_FULL_FOR_RATIO = 200
WEAK_RATIO = 0.90

#: 产物精简率阈值（精简版字数 / 完整版字数）
HTML_FAIL, HTML_WARN = 0.60, 0.55

RULES = [
    ('C1', '双档覆盖', 'sections 与 slots 是否都写了 compact 档'),
    ('C2', '逐段精简度', 'compact 相对 full 是否真的短了'),
    ('C3', '约束不丢', 'full 里含强制约束的数字是否都活到 compact'),
    ('C4', '证据不降级', 'compact 的证据标记是否被压缩掉'),
    ('C5', '产物精简率', '精简版正文 / 完整版正文（需 --full-html/--compact-html）'),
]


# ---------------------------------------------------------------- 文本工具

def strip_html(s):
    s = re.sub(r'</(?:p|td|tr|li|h[1-6]|div)>|<br\s*/?>', '\n', str(s), flags=re.I)
    return re.sub(r'<[^>]+>', '', s)


def split_sents(text):
    return [p.strip() for p in re.split(r'(?<=[。；;\n])', text) if p.strip()]


def vis_len(s):
    """可见字数：去标签、去空白。"""
    return len(re.sub(r'\s+', '', strip_html(s)))


def body_pair(raw):
    """把 `slot.body` / `sections.<k>` 归一成 (full, compact, 是否有独立 compact 档)。"""
    if isinstance(raw, dict):
        full = str(raw.get('full') or '')
        comp = str(raw.get('compact') or '')
        return full, (comp or full), bool(comp.strip())
    return str(raw or ''), str(raw or ''), False


# ---------------------------------------------------------------- 规则实现

def collect(facts):
    """把事实源里所有「双档单元」拍平：[(位置, full, compact, 有独立档, 类别)]。

    类别 `slot` / `section` 分开，是因为两者的判据不一样：slot 是**执行单元**
    （约束必须就地保留，见 C3），section 是**资料区**（允许跨段组织、允许整段不展开）。
    """
    units = []
    for di, day in enumerate(facts.get('days', []), 1):
        for slot in day.get('slots', []):
            f, c, dual = body_pair(slot.get('body'))
            units.append(('D%d %s' % (di, slot.get('time', '?')), f, c, dual, 'slot'))
    for sid, sec in (facts.get('sections') or {}).items():
        if isinstance(sec, dict) and not any(k in sec for k in ('full', 'compact')):
            continue
        f, c, dual = body_pair(sec)
        units.append(('sec:%s' % sid, f, c, dual, 'section'))
    return units


def rule_c1(units):
    no_dual = [u for u in units if not u[3]]
    total, dual = len(units), len(units) - len(no_dual)
    if total == 0:
        return 'FAIL', '事实源里没有任何可双档的单元', []
    if dual == 0:
        return 'FAIL', ('%d 个单元**一个都没写 compact 档**——「精简版」会与完整版一模一样'
                        '（渲染器会打印提醒，但提醒拦不住交付）' % total), []
    if no_dual:
        return 'WARN', '%d/%d 个单元写了 compact 档，缺档：%s' % (
            dual, total, '、'.join(u[0] for u in no_dual[:8])
            + ('…' if len(no_dual) > 8 else '')), []
    return 'PASS', '%d/%d 个单元都有 compact 档' % (dual, total), []


def rule_c2(units):
    """compact 相对 full 是否真的短了。

    **只报警不阻断，是刻意的分工**：一个 slot 压缩得少，可能有正当理由（这段本身
    就没有可删的修辞）。真正该被按死的是「拿完整版冒充精简版」——那件事由**产物层**
    C5 负责（精简版字数 / 完整版字数 > 60% → FAIL），因为它衡量才是整篇效果。
    换句话说：这里是**提示**，C5 才是**闸门**。改这条阈值前先想清楚是谁在拦冒充。
    （2026-09-28 审查 L7 项：把「事实源层只 WARN」如实写下来，免得下一轮被当成漏洞。）
    """
    weak = []
    for loc, full, comp, dual, _ in units:
        if OMITTED_MARK in comp:          # 有意留白的资料区，不比较
            continue
        nf, nc = vis_len(full), vis_len(comp)
        if nf >= MIN_FULL_FOR_RATIO and nc >= nf * WEAK_RATIO:
            weak.append((loc, nf, nc, nc / float(nf)))
    if weak:
        return 'WARN', ('%d 个单元的 compact 档几乎没短（full ≥ %d 字却压不到 %d%%）：%s'
                        % (len(weak), MIN_FULL_FOR_RATIO, WEAK_RATIO * 100,
                           '、'.join('%s %d→%d' % (l, a, b) for l, a, b, _ in weak[:6]))), []
    return 'PASS', '没有「写了 compact 却几乎没短」的单元', []


def rule_c3(units):
    """强制约束里的数字必须活到 compact 档。

    **只查 slot**：slot 是执行单元，约束要就地保留。`sections` 是资料区，
    允许跨段组织信息（同一事实可能出现在别的板块），按单元查会产生误报。
    """
    fails = []
    for loc, full, comp, dual, kind in units:
        if kind != 'slot' or not dual:
            continue
        have = {re.sub(r'\s+', '', x) for x in NUM_RE.findall(strip_html(comp))}
        for sent in split_sents(strip_html(full)):
            if not CONSTRAINT_RE.search(sent):
                continue
            for num in set(NUM_RE.findall(sent)):
                if re.sub(r'\s+', '', num) not in have:
                    fails.append((loc, num, sent.strip()[:52]))
    if fails:
        return 'FAIL', ('%d 处强制约束里的数字在 compact 档中丢失——'
                        '这类句子（末班 / 停止入场 / 寄放 / 留足余量）'
                        '是路上最不能丢的，短不能成为理由' % len(fails)), fails
    return 'PASS', '约束句里的数字在 compact 档中都还在', []


def rule_c4(units):
    fails, warns = [], []
    for loc, full, comp, dual, _ in units:
        if not dual or OMITTED_MARK in comp:   # 有意留白的不算降标准
            continue
        nf = len(EVIDENCE_RE.findall(full))
        nc = len(EVIDENCE_RE.findall(comp))
        if nf and nc == 0:
            fails.append((loc, nf, nc))
        elif nf >= 4 and nc < nf * 0.5:
            warns.append((loc, nf, nc))
    if fails:
        return 'FAIL', ('%d 个单元 full 有证据标记、compact 一个都没有——'
                        '精简版删掉证据等级 = 降标准交付' % len(fails)), fails
    if warns:
        return 'WARN', ('%d 个单元的证据标记被压掉一半以上（可能正常，也可能删多了）：%s'
                        % (len(warns), '、'.join('%s %d→%d' % w for w in warns[:6]))), warns
    return 'PASS', '证据标记没有被压掉', []


def rule_c5(full_html, comp_html):
    if not (full_html and comp_html):
        return 'SKIP', '未提供 --full-html / --compact-html，跳过产物精简率', []
    def vis(p):
        s = Path(p).read_text(encoding='utf-8')
        s = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', s, flags=re.S | re.I)
        return len(re.sub(r'\s+', '', re.sub(r'<[^>]+>', '', s)))
    a, b = vis(full_html), vis(comp_html)
    if not a:
        return 'SKIP', '完整版为空，跳过', []
    r = b / float(a)
    msg = '完整版 %d 字 → 精简版 %d 字（%.1f%%）' % (a, b, r * 100)
    if r > HTML_FAIL:
        return 'FAIL', msg + '——超过 %.0f%%，精简版形同虚设' % (HTML_FAIL * 100), []
    if r > HTML_WARN:
        return 'WARN', msg + '——略高于 %.0f%%' % (HTML_WARN * 100), []
    return 'PASS', msg, []


# ---------------------------------------------------------------- 主流程

def main(argv=None):
    ap = argparse.ArgumentParser(
        description='精简版核验：双档覆盖 / 精简度 / 强制约束不丢 / 证据不降级')
    ap.add_argument('--facts', required=True, help='路书事实源 JSON')
    ap.add_argument('--full-html', help='完整版 HTML（算产物精简率用）')
    ap.add_argument('--compact-html', help='精简版 HTML（算产物精简率用）')
    ap.add_argument('--json', action='store_true', help='输出机器可读 JSON')
    a = ap.parse_args(argv)

    try:
        facts = json.loads(Path(a.facts).read_text(encoding='utf-8'))
    except Exception as e:                                  # noqa: BLE001
        print('无法读取事实源：%s' % e)
        return 2

    units = collect(facts)
    result = {}
    for key, fn in (('C1', rule_c1), ('C2', rule_c2), ('C3', rule_c3), ('C4', rule_c4)):
        st, msg, items = fn(units)
        result[key] = {'status': st, 'detail': msg, 'items': items}
    st, msg, items = rule_c5(a.full_html, a.compact_html)
    result['C5'] = {'status': st, 'detail': msg, 'items': items}
    fails = [k for k, v in result.items() if v['status'] == 'FAIL']

    if a.json:
        print(json.dumps({'rules': result, 'fail': fails}, ensure_ascii=False, indent=1))
    else:
        print('=' * 100)
        print('精简版核验　%s' % a.facts)
        print('=' * 100)
        for key, name, _ in RULES:
            v = result[key]
            mark = {'PASS': 'OK ', 'WARN': '!  ', 'FAIL': 'X  ', 'SKIP': '-  '}[v['status']]
            print('[%s] %s%-6s %s' % (key, mark, name, v['detail']))
        for loc, num, sent in result['C3']['items'][:10]:
            print('      · [%s] 丢了 %s ← %s' % (loc, num, sent))
        print('-' * 100)
        if fails:
            print('汇总：FAIL %d（%s）—— 精简版不合格，先修再交付'
                  % (len(fails), '、'.join(fails)))
        else:
            print('汇总：FAIL 0 —— 精简版合格')
    return 2 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
