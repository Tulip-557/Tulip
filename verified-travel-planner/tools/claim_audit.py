#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
claim_audit.py — 声明↔留痕比对：机械复核「标了 [A] 的数是否真来自采集」。

为什么需要它
------------
source_audit.py 审的是**标注纪律**（徽章在册、时戳在场、等级够得着），
它管不了「标了 [A] 的那句是否真在所引来源里」——README 待办 P1，本 skill
已知的最大缺口。成因：事实源只存结论、不存原始返回，没有可比对的东西。

本工具是补这个缺口的**第 1 层**，配两半：
  采集侧：每次成功调用都进 client.call_log（amap.py）；amap-snapshot 内嵌
    raw_calls；search-places / nearby-spots 可 --trace 落盘。
  比对侧（本文件）：把留痕里的实采值拿到事实源全文里找。

查什么（v2 范围）
--------------------------
  C1 实采值覆盖   itinerary.segments 的 duration_minutes **与 estimated_cost**
                  （文件 _说明 自声明「全部来自高德实采」）必须逐个出现在
                  事实源全文中（按数值规范比对）。找不到 = 采集到的数没如实
                  进路书——采集 16 写成 15、实采 25 写成 20 这类数字幻觉，
                  在这里现形。FAIL。
  C2 声明回查     事实源里含「高德/amap」字样的字符串中的数值（分钟/小时/
                  公里/米/元/**¥·￥ 前缀的票价**），逐个回查留痕池；v2 的池
                  补了**单位换算容忍**（米↔公里、分钟↔小时、快照距离的两种
                  舍入形），查不到的仍列 UNVERIFIED——留痕没跟上的（示例未
                  随附快照、正文摘录未纳）如实列出，不判死。
                  ⚠️ 票价此前**整类隐形**：事实源写 `过路费 ¥17`，而 UNIT_NUM
                  只认「17 元」——实测东莞 103 处里 `元` 是 0 条，不是都对得上，
                  是根本没被看见（2026-10-01 补 FARE_NUM）。
  C3 留痕健康     itinerary 无「实采」声明、快照无 raw_calls、周边采集缺
                  provider 字段、留痕文件空转 → WARN（留痕缺位，声明只能停在
                  「说得出来源」）。

不查（边界，写在这里是为了不被读成保证）
--------------------------------------
· 非高德来源的声明（官网门票价等）——网页那半归 echo_audit（来源回声核查），
  独立性归 cross_check；三个闸门合起来才是「内容真实」的三层链。
  ⚠️ C2 里列出的 ¥ 条目**既有高德侧可补留痕的**（过路费/公交票价），
  **也有本链路结构上够不着的**（官网门票价）——别把 UNVERIFIED 数量读成
  「都该被本工具查出来」。
· 快照/留痕与世界是否一致——留痕只证明「声明与采集一致」，不证明「采集对」
· nearby 的候选点名/评分/距离、快照的城级路线值——采集了但没进路书是正常
  事（顺道候选只列不判断），只报告不计 FAIL

用法
----
    python tools/claim_audit.py --facts 路书_X.json --itinerary itinerary_X.json
                                [--nearby nearby_X.json ...]
                                [--snapshot 快照_X.json ...] [--snapshot 留痕_X.json ...]
                                [--json]
    `--snapshot` 按**形状**自动识别（快照形 / `--trace` 落的 trace 形），
    可重复给多个文件——ship.py 就是这么按命名约定逐个传的。
退出码：0 = 无 FAIL ｜ 2 = 有 FAIL ｜ 1 = 用法/读取错误
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

NUM_TOKEN = re.compile(r'\d+(?:\.\d+)?')
UNIT_NUM = re.compile(r'(\d+(?:\.\d+)?)\s*(分钟|小时|公里|千米|米|元)')
#: 票价在事实源里写的是 `过路费 ¥17` / `约 ¥3`——**不是**「17 元」。
#: UNIT_NUM 只认「数字 + 单位」，所以票价整类从未进入 C2 回查：
#: 实测东莞 103 处 UNVERIFIED 里 `元` 是 **0** 条——不是票价都对得上，
#: 是它们根本没被看见（2026-10-01 补）。
FARE_NUM = re.compile(r'[¥￥]\s*(\d+(?:\.\d+)?)')
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
        self.durations = set()          # 分钟（itinerary.segments / 快照路线 实采）
        self.distances_m = set()        # 米（快照路线 / 周边采集）
        self.costs_declared = set()     # 元（itinerary.segments 实采，C1 硬面）
        self.costs = set()              # 元（快照路线，蒸馏值池）
        self.derived = set()            # **池专用**（C1 不看）：方向留痕的秒→分换算形
        self.names = []                 # POI 名（周边采集）
        self.raw_texts = []             # 原始返回体文本（快照 raw_calls / trace calls）
        self.itinerary_declared = None  # itinerary 是否自声明「实采」
        self.snapshot_raw = None        # 快照是否内嵌 raw_calls
        self.warnings = []

    @property
    def empty(self) -> bool:
        return not (self.durations or self.distances_m or self.costs
                    or self.costs_declared or self.names or self.raw_texts)


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
        # v2：打车费/过路费同样自声明「实采」——声明了却不硬查，等于没查。
        cost = seg.get('estimated_cost')
        if isinstance(cost, (int, float)) and cost >= 0:
            ev.costs_declared.add(cost)


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
    """C1：实采值（段时长 + 段成本）必须逐个出现在事实源全文。

    返回 (pass, fail) 列表。
    """
    ok, bad = [], []
    for v in sorted(ev.durations):
        if _fmt(v) in tokens or str(v) in tokens:
            ok.append('段时长 %s 分钟' % _fmt(v))
        else:
            bad.append('实采段时长 %s 分钟在事实源全文中找不到——'
                       '被改数、被丢段，或该段已不进路书（修数据或删留痕）' % _fmt(v))
    for v in sorted(ev.costs_declared):
        if _fmt(v) in tokens or str(v) in tokens:
            ok.append('段成本 %s 元' % _fmt(v))
        else:
            bad.append('实采段成本 %s 元在事实源全文中找不到——'
                       '被改数或被丢（修数据或删留痕）' % _fmt(v))
    return ok, bad


def _harvest_route_response(response, ev: Evidence) -> None:
    """从方向接口返回体里取蒸馏值（距离 / 时长 / 过路费·票价）。

    为什么必须收：原始体里时长是**秒**（`"duration":"960"`），而路书写的
    是「16 分钟」；距离是**米**（`"distance":"9500"`），路书写「9.5 公里」。
    C2 的换算只作用在**声明侧**（公里→米、小时→分钟），够不着「池子这一侧」
    的秒→分——不收它，`--trace` 落的方向留痕就只有票价能用，时长永远挂着。

    秒→分用 `math.ceil`，与 `amap.py` 的 `_minutes` **逐字一致**：这里复算的
    是引擎已经写进路书的那个数，取整方式差一点就会自己造出误报。
    """
    route = response.get('route') or {}
    for path in (route.get('paths') or []):
        if isinstance(path, dict):
            _add_route_triple(ev, path.get('duration'), path.get('distance'),
                              path.get('tolls'))
    for transit in (route.get('transits') or []):
        if isinstance(transit, dict):
            _add_route_triple(ev, transit.get('duration'), transit.get('distance'),
                              transit.get('cost'))


def _add_route_triple(ev: Evidence, duration, distance, cost) -> None:
    """把一段方向结果的蒸馏值放进**池**。

    ⚠️ 秒→分只进 `ev.derived`（池专用），**绝不进 `ev.durations`**：
    C1 遍历的是 `durations`，把它塞进去就等于「查过的段必须在路书里」——
    而查了不用是正常事（顺道候选只列不判断），那样会立刻开始误伤。
    """
    seconds = _to_number(duration)
    if seconds is not None:
        ev.derived.add(_fmt(max(0, math.ceil(seconds / 60))))
    meters = _to_number(distance)
    if meters is not None:
        ev.distances_m.add(int(meters))
    fare = _to_number(cost)
    if fare is not None:
        ev.costs.add(fare)


def _to_number(value):
    if value in (None, '', []):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_trace(path: Path, ev: Evidence) -> None:
    """读 `--trace` 形留痕：{provider, generated_at, call_count, calls[]}。

    `calls[].response` 是**完整原始返回体**——方向接口的 distance / duration /
    tolls / cost 都在里面，所以直接进 raw_texts 就能被 C2 的池吃到。
    """
    d = json.loads(path.read_text(encoding='utf-8'))
    if str(d.get('provider') or '') != 'amap':
        ev.warnings.append('W3 %s 非 amap 留痕——不在本工具比对范围' % path.name)
        return
    calls = d.get('calls')
    if not isinstance(calls, list):
        ev.warnings.append('W5 %s 没有 calls 数组——留痕文件形状不对' % path.name)
        return
    if not calls:
        # 空留痕要说出来：它会让 C2 池子变空，看起来像「全都查不到」
        ev.warnings.append(
            'W5 %s 里 0 条成功调用（call_count=%s）——本次采集没留下任何证据，'
            '不是「查了都对得上」' % (path.name, d.get('call_count')))
        return
    for call in calls:
        if isinstance(call, dict):
            response = call.get('response')
            try:
                ev.raw_texts.append(json.dumps(response, ensure_ascii=False))
            except (TypeError, ValueError):
                pass
            # 蒸馏值也收一份：方向接口的秒/米要换成分钟/公里才比得上路书
            endpoint = str(call.get('endpoint') or '')
            if '/direction/' in endpoint and isinstance(response, dict):
                _harvest_route_response(response, ev)


def load_evidence(path: Path, ev: Evidence) -> str:
    """**按形状**加载留痕文件，返回 'trace' / 'snapshot' / 'unknown'。

    两种形状各有出处：
      · trace 形：`{provider, generated_at, call_count, calls[]}`——`--trace` 落盘
      · 快照形：`{provenance:{provider:amap}, routes[], raw_calls[]}`——amap-snapshot

    为什么必须按形状认：只认快照形的话，把 `--trace` 落的文件当 `--snapshot`
    传会命中 W3 被**静默忽略**——一份真留痕被丢掉却不报错，正是本工具最该
    避免的形状（2026-10-01 修）。
    """
    d = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(d.get('calls'), list):
        load_trace(path, ev)
        return 'trace'
    if 'provenance' in d or 'raw_calls' in d or 'routes' in d:
        load_snapshot(path, ev)
        return 'snapshot'
    ev.warnings.append(
        'W3 %s 既不是 trace 形也不是快照形——无法识别，未加载' % path.name)
    return 'unknown'


def _excerpt(text: str, start: int, end: int, width: int = 52) -> str:
    """以**命中处为中心**截一段，别只给字符串开头。

    事实源里的 section 正文是整块 HTML，开头 60 字常常是 `<div class=...><h1>`
    这类标签——人对着那段找不着被报的数，会以为工具报错了。
    """
    pad = max(0, width - (end - start))
    left = max(0, start - pad // 2)
    right = min(len(text), end + (pad - (start - left)))
    fragment = ' '.join(text[left:right].split())
    return ('…' if left else '') + fragment + ('…' if right < len(text) else '')


def check_c2_claims(facts_strings, ev: Evidence):
    """C2：高德句中的数值回查留痕池。返回 UNVERIFIED 列表（不判死）。

    v2 的池补了单位换算容忍：距离的 米↔公里（含两种舍入形）、
    时长的 分钟↔小时（含 1.5 小时这类小数形）。换算仍对不上的才算
    「无留痕可对」——这能把 UNVERIFIED 清单从「换算噪声」缩到真缺口。

    两类数值进回查：带中文单位的（分钟/小时/公里/千米/米/元），
    以及 **¥/￥ 前缀的票价**——后者此前整类隐形（见 FARE_NUM 注释）。
    """
    pool_numbers = set()
    for v in ev.durations:
        pool_numbers.add(_fmt(v))
        pool_numbers.add(_fmt(round(float(v) / 60.0, 1)))       # 分钟 → 小时
        pool_numbers.add(_fmt(round(float(v) / 60.0, 2)))
    for m in ev.distances_m:
        pool_numbers.add(_fmt(m))
        km1 = _fmt(round(m / 1000.0, 1))
        km2 = _fmt(round(m / 1000.0))
        pool_numbers.update((km1, km2))
        try:                                                     # 公里小数不动点
            pool_numbers.add(_fmt(float(m) / 1000.0))
        except (ValueError, OverflowError):
            pass
    for c in ev.costs:
        pool_numbers.add(_fmt(c))
    pool_numbers.update(ev.derived)      # 方向留痕的秒→分换算形（池专用，C1 不看）
    for text in ev.raw_texts:
        pool_numbers.update(m.group(0) for m in NUM_TOKEN.finditer(text))

    unverified = []
    for s in facts_strings:
        if not any(w in s for w in AMAP_WORDS):
            continue
        for m in UNIT_NUM.finditer(s):
            val = _fmt(float(m.group(1)))
            unit = m.group(2)
            cands = {val}
            if unit in ('公里', '千米'):
                cands.update((_fmt(float(val) * 1000),))          # 公里 → 米
            if unit == '小时':
                cands.update((_fmt(float(val) * 60),))            # 小时 → 分钟
            if not (cands & pool_numbers):
                unverified.append('%s%s ｜ %s'
                                  % (m.group(1), unit, _excerpt(s, m.start(), m.end())))
        for m in FARE_NUM.finditer(s):
            if _fmt(float(m.group(1))) not in pool_numbers:
                unverified.append('¥%s ｜ %s'
                                  % (m.group(1), _excerpt(s, m.start(), m.end())))
    return unverified


def main():
    ap = argparse.ArgumentParser(
        description='声明↔留痕比对：机械复核 [A] 声明是否真来自采集（P1 v2）')
    ap.add_argument('--facts', required=True, help='路书事实源 JSON')
    ap.add_argument('--itinerary', help='行程留痕（segments.duration_minutes 实采）')
    ap.add_argument('--nearby', action='append', default=[],
                    help='周边采集留痕（可多次）')
    ap.add_argument('--snapshot', action='append', default=[],
                    help='amap-snapshot 快照或 --trace 落的留痕（可多次；'
                         '形状自动识别，给哪种都不会被静默丢掉）')
    ap.add_argument('--json', action='store_true', help='输出机器可读 JSON')
    a = ap.parse_args()

    facts_path = Path(a.facts)
    if not facts_path.is_file():
        print('事实源不存在：%s' % facts_path)
        return 1
    ev = Evidence()
    loaded = 0
    # 留痕文件由 ship.py 按命名约定**自动发现**，所以读不动的那份必须
    # 指名道姓地报出来——裸 traceback 会让人对着七八个文件猜是哪个坏了。
    try:
        if a.itinerary:
            load_itinerary(Path(a.itinerary), ev)
            loaded += 1
        for n in a.nearby:
            load_nearby(Path(n), ev)
            loaded += 1
        for s in a.snapshot:
            load_evidence(Path(s), ev)
            loaded += 1
    except OSError as exc:
        print('留痕文件读不了：%s' % exc)
        return 1
    except json.JSONDecodeError as exc:
        print('留痕文件不是合法 JSON（%s）——修好它，或把不该被发现的文件移走' % exc)
        return 1
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
        'scope': ('v2：C1 硬查「实采段时长与段成本未如实进路书」；C2 的 '
                  'UNVERIFIED 是诚实缺口不是 FAIL（快照未随附/正文摘录未纳）；'
                  'C2 已纳入 ¥/￥ 前缀的票价，其中**官网门票价属本链路结构上'
                  '够不着的**那部分（归 echo_audit / cross_check 那两层）；'
                  '快照/留痕证明「声明与采集一致」，不证明「采集与世界一致」'
                  '——后者由 echo_audit（来源回声）与 cross_check（独立源）接力。'),
    }

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 2

    print('=' * 74)
    print('声明↔留痕比对（claim_audit v2）｜ 事实源：%s' % facts_path.name)
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
