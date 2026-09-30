#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
claim_audit.py — 声明↔留痕比对：机械复核「标了 [A] 的数是否真来自采集」。

为什么需要它
------------
source_audit.py 审的是**标注纪律**（徽章在册、时戳在场、等级够得着），
它管不了「标了 [A] 的那句是否真在所引来源里」——README 待办 P1，本 skill
已知的最大缺口。成因：事实源只存结论、不存原始返回，没有可比对的东西。

本工具是补这个缺口的第一层（v1），配两半：
  采集侧：每次成功调用都进 client.call_log（amap.py）；amap-snapshot 内嵌
    raw_calls；search-places / nearby-spots 可 --trace 落盘。
  比对侧（本文件）：把留痕里的实采值拿到事实源全文里找。

查什么（v1 范围，宁缺毋滥——每条 FAIL 都必须钉得住）
--------------------------
  C1 实采值覆盖   itinerary.segments 的 duration_minutes（文件 _说明 自声明
                  「全部来自高德驾车路线实采」）必须逐个出现在事实源全文中
                  （按数值规范比对）。找不到 = 采集到的数没如实进路书
                  ——采集 16 写成 15 这类数字幻觉，在这里现形。FAIL。
  C2 声明回查     事实源里含「高德/amap」字样的字符串中的数值（分钟/公里/
                  米/元），逐个回查留痕池；查不到的列 UNVERIFIED——v1 的
                  留痕面不含驾车距离、公交票价等，如实列出，不判死。
  C3 留痕健康     itinerary 无「实采」声明、快照无 raw_calls、周边采集缺
                  provider 字段 → WARN（留痕缺位，声明只能停在「说得出来源」）。

不查（边界，写在这里是为了不被读成保证）
--------------------------------------
· 非高德来源的声明（官网门票价等）——归 source_audit 的 E 规则
· 快照/留痕与世界是否一致——留痕只证明「声明与采集一致」，不证明「采集对」
· nearby 的候选点名/评分/距离、快照的城级路线值——采集了但没进路书是正常
  事（顺道候选只列不判断），只报告不计 FAIL

用法
----
    python tools/claim_audit.py --facts 路书_X.json --itinerary itinerary_X.json
                                [--nearby nearby_X.json ...] [--snapshot 快照.json]
                                [--json]
退出码：0 = 无 FAIL ｜ 2 = 有 FAIL ｜ 1 = 用法/读取错误
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NUM_TOKEN = re.compile(r'\d+(?:\.\d+)?')
UNIT_NUM = re.compile(r'(\d+(?:\.\d+)?)\s*(分钟|小时|公里|千米|米|元)')
AMAP_WORDS = ('高德', 'amap')
REALDATA_MARKS = ('实采', '高德')


def _fmt(f: float) -> str:
    s = ('%f' % float(f)).rstrip('0').rstrip('.')
    return s if s else '0'


def iter_strings(obj):
    """深度遍历 JSON，产出全部字符串值（跳过 _说明/_使用说明 类自述键）。"""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_strings(v)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.startswith('_'):
                continue
            yield from iter_strings(v)


def number_tokens(text: str) -> set:
    """文本里出现的全部数值（原样 + 常见舍入形），供「值在文中」判断。"""
    vals = set()
    for m in NUM_TOKEN.finditer(text):
        s = m.group(0)
        vals.add(s)
        if '.' in s:
            f = float(s)
            vals.add(_fmt(f))
            vals.add(_fmt(round(f, 1)))
            vals.add(_fmt(round(f)))
    return vals


class Evidence:
    """留痕池：从各证据文件里取出的「机器实采值」。"""

    def __init__(self):
        self.durations = set()          # 分钟（itinerary.segments 实采）
        self.distances_m = set()        # 米（快照路线 / 周边采集）
        self.costs = set()              # 元（快照路线）
        self.names = []                 # POI 名（周边采集）
        self.raw_texts = []             # 原始返回体文本（快照 raw_calls）
        self.itinerary_declared = None  # itinerary 是否自声明「实采」
        self.snapshot_raw = None        # 快照是否内嵌 raw_calls
        self.warnings = []

    @property
    def empty(self) -> bool:
        return not (self.durations or self.distances_m or self.costs
                    or self.names or self.raw_texts)


def load_itinerary(path: Path, ev: Evidence) -> None:
    d = json.loads(path.read_text(encoding='utf-8'))
    segments = d.get('segments') or []
    note = str(d.get('_说明') or '')
    ev.itinerary_declared = any(m in note for m in REALDATA_MARKS)
    if not ev.itinerary_declared:
        ev.warnings.append(
            'W1 %s 没有「实采」自声明（_说明）——段时长来源不明，'
            'C1 覆盖审计降为跳过' % path.name)
        return
    for seg in segments:
        if isinstance(seg, dict) and seg.get('duration_minutes') is not None:
            ev.durations.add(seg['duration_minutes'])


def load_nearby(path: Path, ev: Evidence) -> None:
    d = json.loads(path.read_text(encoding='utf-8'))
    if str(d.get('provider') or '') != 'amap':
        ev.warnings.append('W2 %s 缺 provider=amap 字段——来源身份不明' % path.name)
    for stop in d.get('stops') or []:
        for place in (stop.get('places') or []):
            name = str(place.get('name') or '').strip()
            if name:
                ev.names.append(name)
            dist = place.get('distance_meters')
            if isinstance(dist, (int, float)) and dist > 0:
                ev.distances_m.add(dist)


def load_snapshot(path: Path, ev: Evidence) -> None:
    d = json.loads(path.read_text(encoding='utf-8'))
    prov = d.get('provenance') or {}
    if str(prov.get('provider') or '') != 'amap':
        ev.warnings.append('W3 %s 非 amap 快照——不在本工具比对范围' % path.name)
        return
    raw_calls = d.get('raw_calls')
    ev.snapshot_raw = raw_calls is not None
    if not ev.snapshot_raw:
        ev.warnings.append(
            'W4 %s 未内嵌 raw_calls（生成时用了 --no-keep-raw 或旧版工具）——'
            '原始返回体缺位，只能比对蒸馏值' % path.name)
    else:
        for call in raw_calls:
            try:
                ev.raw_texts.append(json.dumps(call.get('response'), ensure_ascii=False))
            except (TypeError, ValueError):
                pass
    for route in d.get('routes') or []:
        if not isinstance(route, dict):
            continue
        if route.get('duration_minutes') is not None:
            ev.durations.add(route['duration_minutes'])
        if route.get('distance_meters') is not None:
            ev.distances_m.add(route['distance_meters'])
        if route.get('estimated_cost') is not None:
            ev.costs.add(route['estimated_cost'])


def check_c1_coverage(facts_text: str, tokens: set, ev: Evidence):
    """C1：实采段时长必须逐个出现在事实源全文。返回 (pass, fail) 列表。"""
    ok, bad = [], []
    for v in sorted(ev.durations):
        if _fmt(v) in tokens or str(v) in tokens:
            ok.append('段时长 %s 分钟' % _fmt(v))
        else:
            bad.append('实采段时长 %s 分钟在事实源全文中找不到——'
                       '被改数、被丢段，或该段已不进路书（修数据或删留痕）' % _fmt(v))
    return ok, bad


def check_c2_claims(facts_strings, ev: Evidence):
    """C2：高德句中的数值回查留痕池。返回 UNVERIFIED 列表（不判死）。"""
    pool_numbers = set()
    for v in ev.durations:
        pool_numbers.add(_fmt(v))
    for m in ev.distances_m:
        pool_numbers.add(_fmt(m))
        pool_numbers.add(_fmt(round(m / 1000.0, 1)))
    for c in ev.costs:
        pool_numbers.add(_fmt(c))
    for text in ev.raw_texts:
        pool_numbers.update(m.group(0) for m in NUM_TOKEN.finditer(text))

    unverified = []
    for s in facts_strings:
        if not any(w in s for w in AMAP_WORDS):
            continue
        for m in UNIT_NUM.finditer(s):
            val = _fmt(float(m.group(1)))
            if val not in pool_numbers:
                unverified.append('%s%s ｜ %s'
                                  % (m.group(1), m.group(2), s.strip()[:60]))
    return unverified


def main():
    ap = argparse.ArgumentParser(
        description='声明↔留痕比对：机械复核 [A] 声明是否真来自采集（P1 v1）')
    ap.add_argument('--facts', required=True, help='路书事实源 JSON')
    ap.add_argument('--itinerary', help='行程留痕（segments.duration_minutes 实采）')
    ap.add_argument('--nearby', action='append', default=[],
                    help='周边采集留痕（可多次）')
    ap.add_argument('--snapshot', help='amap-snapshot 快照（含 raw_calls 更佳）')
    ap.add_argument('--json', action='store_true', help='输出机器可读 JSON')
    a = ap.parse_args()

    facts_path = Path(a.facts)
    if not facts_path.is_file():
        print('事实源不存在：%s' % facts_path)
        return 1
    ev = Evidence()
    loaded = 0
    if a.itinerary:
        load_itinerary(Path(a.itinerary), ev)
        loaded += 1
    for n in a.nearby:
        load_nearby(Path(n), ev)
        loaded += 1
    if a.snapshot:
        load_snapshot(Path(a.snapshot), ev)
        loaded += 1
    if loaded == 0:
        print('没有任何留痕文件（--itinerary/--nearby/--snapshot 至少给一个）')
        return 1

    facts = json.loads(facts_path.read_text(encoding='utf-8'))
    facts_strings = list(iter_strings(facts))
    facts_text = '\n'.join(facts_strings)
    tokens = number_tokens(facts_text)

    c1_ok, c1_bad = check_c1_coverage(facts_text, tokens, ev)
    c2_unverified = check_c2_claims(facts_strings, ev)

    result = {
        'facts': str(facts_path),
        'evidence_files': loaded,
        'c1_coverage': {'pass': c1_ok, 'fail': c1_bad},
        'c2_unverified': c2_unverified,
        'warnings': ev.warnings,
        'ok': not c1_bad,
        'scope': ('v1：C1 只钉「实采段时长未如实进路书」；C2 的 UNVERIFIED 是'
                  '诚实缺口不是 FAIL；快照/留痕证明「声明与采集一致」，'
                  '不证明「采集与世界一致」。'),
    }

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 2

    print('=' * 74)
    print('声明↔留痕比对（claim_audit v1）｜ 事实源：%s' % facts_path.name)
    print('=' * 74)
    print('  C1 实采值覆盖：PASS %d ｜ FAIL %d' % (len(c1_ok), len(c1_bad)))
    for row in c1_ok:
        print('    [OK] %s 在事实源中可寻' % row)
    for row in c1_bad:
        print('    [X ] %s' % row)
    print('  C2 声明回查：%d 个高德句数值无留痕可对（UNVERIFIED，不判死）'
          % len(c2_unverified))
    for row in c2_unverified[:8]:
        print('    [? ] %s' % row)
    if len(c2_unverified) > 8:
        print('    …另有 %d 处' % (len(c2_unverified) - 8))
    if ev.warnings:
        print('  C3 留痕健康：')
        for w in ev.warnings:
            print('    [%s]' % w)
    print('-' * 74)
    if c1_bad:
        print('[X] FAIL %d 处——实采值没有如实进路书，先修再交付' % len(c1_bad))
        return 2
    print('[OK] 实采值全部可寻（%d 个）｜ 声明与留痕一致' % len(c1_ok))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
