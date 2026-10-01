#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
render_html.py — 路书「数据与模板分离」渲染器

为什么需要它
------------
上游见好（jianhao-travel-planner）已把 HTML 骨架固化下来，但落地方式是
「复制骨架 → 手改 4 类常量 → 手填 13 个区块」。它自己在 SKILL.md 里写明
理想态是「数据与模板分离（md=唯一事实源，HTML=渲染产物）」——本脚本就是
把那一步补上：

    单一事实源 ludbook.json  ──render_html.py──▶  路书_XX.html

骨架的 <style> / 三个 <script> / IA / 导航 全部由基准文件原样带入，
因此「CSS 指纹一致」是结构上不可能的出错项，而不是靠提示词祈求。

本脚本只自动化「机械、易错、必须一致」的部分：
  ① 10 类基准占位符替换（缺值即报错，从根上杜绝残留 【】）
  ② localStorage 城市码（__CITY__ → <城市码>）
  ③ TRIP 日期映射 + 当日模式基准日
  ④ 侧栏 day 导航 + day 区块数量随行程天数增删
  ⑤ 逐日 .day 块（.day-head / .photos / .note-row / .slot / .warn-line）
  ⑥ 结构钩子：.check li 自动补 <input>、.tips .tip 自动包一层元素
  ⑦ 变体：friend 版删 #intel（单 <style>）；self 版保留（双 <style>）

创作性文案（门票/贴士/美食/预算/应急…）由事实源里的 HTML 片段直接注入，
不强行 DSL 化——文案是创作，不是数据。

用法
----
    python render_html.py 路书_成都.json -o 路书_成都.html [--base 骨架.html] [--quiet]
    python render_html.py 路书_成都.json --verify     # 渲染后立即跑一致性校验

退出码：0 = 渲染成功（--verify 时另需一致性通过）；1 = 数据不合规；2 = 文件问题。
"""
import io
import os
import re
import sys
import json
import base64
import argparse
import mimetypes
import html as _html
from datetime import datetime

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BASE = os.path.join(SKILL_ROOT, "assets", "路书_基准骨架.html")

# ---- 终端符号自适应：中文 Windows 黑框(GBK)认不出 ✅❌ 时退 ASCII，防崩框
_SYM_FALLBACK = str.maketrans({
    '✅': '[OK]', '✓': '[OK]', '√': '[OK]',
    '❌': '[X]', '✗': '[X]', '×': '[X]',
    '→': '->', '■': '[*]', '●': '[*]', '·': '.',
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


# ---------------- 基准占位符 → 事实源字段 ----------------
# 键必须与 assets/路书_基准骨架.html 里出现的字面量完全一致。
#
# ⚠️ 上游 consistency.py 的占位符正则写成 【[^】]{1,24}】，限长 24 字符——骨架页脚
#    那条 40+ 字符的配图说明占位符因此**从未被纳入检查**（残留了也没人拦）。
#    本脚本改用无长度上限的扫描，并在渲染前做「未知占位符即报错」的硬闸。
PLACEHOLDER_MAP = {
    '【目的地】': 'destination',
    '【N 天 N 晚】': 'nights',
    '【人数结构】': 'party',
    '【出发地】': 'origin',
    '【日期区间】': 'date_range',
    '【实查日期】': 'verified_date',
    '【行程主题】': 'trip_theme',
    '【图源】': 'photos_source',
    '【自绘示意图说明：非等比例、仅供参考】': 'sketch_note',
    '【逐张写明哪些是当地实景照、哪些是同题材示意（示意项须在 caption 标「（示意）」）】': 'photos_note',
    # 【当日主题】 由侧栏导航按天替换，不走本表（见 DYNAMIC_PLACEHOLDERS）
}
DYNAMIC_PLACEHOLDERS = {'【当日主题】'}

REQUIRED_META = ('destination', 'city_code', 'origin', 'date_range',
                 'party', 'verified_date', 'nights', 'trip_theme', 'photos_note')

# 非 day 区块：IA 顺序与基准一致，且全部必填（缺一即拒绝渲染）
REQUIRED_SECTIONS = ('overview', 'drive', 'boost', 'ticket',
                     'tips', 'stay', 'cost', 'sos', 'eat', 'gift')

CITY_CODE_RE = re.compile(r'^[a-z]{2,8}$')
DAY_RE = re.compile(r'^day(\d+)$')

# 备注行图标：kind → (图标, 附加 class)
NOTE_KINDS = {
    'summary': ('🧭', ''),
    'metro': ('🚇', ''),
    'photo': ('📷', ' photo'),
    'stay': ('🛏', ''),
    'food': ('🍜', ''),
    'warn': ('⚠️', ''),
}

#: 精简版逐日注释里**保持展开**的 kind。
#:
#: 判据与区块折叠一致——「出发前对一遍，还是当天随时用」：
#: `food`（今天吃什么）/ `stay`（今晚住哪）是当天随时要用的；
#: `metro`（今日交通汇总）在 slot 里已逐段出现，收进 `<details>` 即可；
#: `warn` 永不折叠。
_NOTE_KEEP_IN_COMPACT = ('food', 'stay', 'warn')


class DataError(Exception):
    """事实源不合规——渲染中止，不产出半成品。"""


def esc(v):
    return _html.escape(str(v), quote=True)


def read(p):
    if not os.path.exists(p):
        emit('FATAL 文件不存在: %s' % p)
        sys.exit(2)
    return io.open(p, encoding='utf-8', errors='replace').read()


def weekday_cn(date_str):
    try:
        return '周' + '一二三四五六日'[datetime.strptime(date_str, '%Y-%m-%d').weekday()]
    except Exception:
        return ''


# ============================ 数据校验 ============================
def _scan_todo(data):
    """扫描未替换的模板占位（以 TODO 开头）。

    模板 `assets/路书事实源模板.json` 是"待填起点"而不是可直接渲染的成品，
    所以从它复制出来的草稿第一遍跑必然带一堆 TODO 报错——这是设计意图。
    问题在于**怎么报**：如果放任它流到渲染阶段，使用者只会看到
    「配图文件不存在：…\\TODO 图片地址或本地路径」这种把人引偏的错。
    所以在这里一次性全列出来，并且跳过 `_` 开头的说明性键。
    """
    found = []

    def walk(value, path):
        if isinstance(value, str):
            if value.strip().upper().startswith('TODO'):
                found.append('%s = %s' % (path, value.strip()[:48]))
        elif isinstance(value, dict):
            for key, sub in value.items():
                if str(key).startswith('_'):
                    continue
                walk(sub, '%s.%s' % (path, key) if path else str(key))
        elif isinstance(value, list):
            for index, sub in enumerate(value):
                walk(sub, '%s[%d]' % (path, index))

    walk(data, '')
    return found


def validate(data):
    """先把事实源查干净再渲染——不合格就不产出半成品。"""
    errs = []
    if not isinstance(data, dict):
        raise DataError('事实源顶层必须是 JSON 对象')

    meta = data.get('meta')
    if not isinstance(meta, dict):
        raise DataError('缺 meta 段')
    for f in REQUIRED_META:
        if not str(meta.get(f) or '').strip():
            errs.append('meta.%s 缺失或为空' % f)
    cc = str(meta.get('city_code') or '')
    if cc and not CITY_CODE_RE.match(cc):
        errs.append('meta.city_code 须为 2-8 位小写字母（现为 %r）' % cc)

    days = data.get('days')
    if not isinstance(days, list) or not days:
        errs.append('days 必须是非空数组')
        days = []
    seen_dates = set()
    for i, d in enumerate(days, 1):
        if not isinstance(d, dict):
            errs.append('days[%d] 不是对象' % i)
            continue
        dt = str(d.get('date') or '')
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', dt):
            errs.append('days[%d].date 须为 YYYY-MM-DD（现为 %r）' % (i, dt))
        elif dt in seen_dates:
            errs.append('days[%d].date %s 重复' % (i, dt))
        else:
            seen_dates.add(dt)
        if not str(d.get('theme') or '').strip():
            errs.append('days[%d].theme（当日主题）缺失' % i)
        slots = d.get('slots')
        if not isinstance(slots, list) or not slots:
            errs.append('days[%d].slots 必须是非空数组（时段卡是逐日主体）' % i)
        else:
            for j, s in enumerate(slots, 1):
                raw = s.get('body') if isinstance(s, dict) else None
                if isinstance(raw, dict):       # 双档：{"full": …, "compact": …}
                    ok = bool(str(raw.get('full') or '').strip())
                else:
                    ok = bool(str(raw or '').strip())
                if not isinstance(s, dict) or not str(s.get('time') or '').strip() or not ok:
                    errs.append('days[%d].slots[%d] 须同时有 time 与 body'
                                '（body 可写字符串，或 {"full":…, "compact":…} 双档）' % (i, j))
        for n in (d.get('notes') or []):
            if not isinstance(n, dict) or n.get('kind') not in NOTE_KINDS:
                errs.append('days[%d].notes 的 kind 须为 %s 之一'
                            % (i, '/'.join(sorted(NOTE_KINDS))))
        for p in (d.get('photos') or []):
            if not isinstance(p, dict) or not str(p.get('src') or '').strip():
                errs.append('days[%d].photos 每张须有 src' % i)

    secs = data.get('sections')
    if not isinstance(secs, dict):
        errs.append('缺 sections 段')
        secs = {}
    for k in REQUIRED_SECTIONS:
        if not str(secs.get(k) or '').strip():
            errs.append('sections.%s（区块 %s）缺失或为空' % (k, k))

    todo = _scan_todo(data)
    if todo:
        shown = '\n     '.join(todo[:20])
        more = ('\n     …另有 %d 处' % (len(todo) - 20)) if len(todo) > 20 else ''
        errs.append('有 %d 处模板占位（TODO）尚未替换：\n     %s%s'
                    % (len(todo), shown, more))

    if errs:
        raise DataError('事实源不合规，共 %d 项：\n   - %s' % (len(errs), '\n   - '.join(errs)))
    return days, secs, meta


# ============================ 片段构造 ============================
def _img_tag(src, cap, meta):
    """远端图直接引用；本地图内联成 base64。

    「本地单文件版 · 断网可开 · 单独发给别人也能看图」是上游写在文档里的核心承诺，
    但只有把本地图真正内联进去才成立——否则 HTML 一旦离开原目录，图全变成裂图。
    在线瘦身版用 --no-inline 关掉内联，让图走远端。
    """
    alt = esc(cap)
    soft = src.lower().startswith(('http://', 'https://', 'data:'))
    if soft or meta.get('_no_inline'):
        return '<img src="%s" alt="%s" loading="lazy">' % (esc(src), alt)

    path = src if os.path.isabs(src) else os.path.join(str(meta.get('_base_dir') or '.'), src)
    if not os.path.exists(path):
        raise DataError('配图文件不存在：%s（事实源里写的是 %r）。'
                        '远端图请写完整 http(s) 地址；本地图按相对事实源所在目录解析。'
                        % (path, src))
    mime = mimetypes.guess_type(path)[0] or 'image/jpeg'
    if not mime.startswith('image/'):
        raise DataError('配图不是图片类型（%s）：%s' % (mime, path))
    try:
        with open(path, 'rb') as fh:
            raw = fh.read()
    except OSError as exc:
        raise DataError('配图读取失败：%s' % path) from exc

    meta['_inlined'] = meta.get('_inlined', 0) + 1
    meta['_inlined_bytes'] = meta.get('_inlined_bytes', 0) + len(raw)
    return '<img src="data:%s;base64,%s" alt="%s" loading="lazy">' % (
        mime, base64.b64encode(raw).decode('ascii'), alt)


def build_day_block(idx, d, meta, compact=False):
    """逐日 .day 块：day-head → photos → notes(summary) → slots → notes → warn

    精简版在 days 层做两件事（**这里是全篇主体，占字数的 45%**）：
      · 配图**收敛为 1 张**；
      · 每个 slot 的 body 支持 `{"full": …, "compact": …}` **双档**——
        与 `sections` 同一套机制、同一个 `_pick_section`。

    **刻意不机器截断正文**：截断会产生语义不完整的句子，还可能把「末班车 / 停止入场」
    这类安全信息切掉。所以精简版**不靠机器硬切，靠人写紧凑档**——但要用机器兜底：
    `tools/compact_check.py` 会验「full 里的强制约束句，compact 是否也说了」。
    """
    parts = []
    dt = str(d['date'])
    parts.append('<div class="day">')
    parts.append('  <div class="day-head">')
    parts.append('    <span class="day-idx">DAY %d / %s</span>' % (idx, esc(dt)))
    parts.append('    <h3>%s</h3>' % esc(d.get('title') or d['theme']))
    parts.append('    <span class="today-badge">今天</span>')
    parts.append('  </div>')

    photos = d.get('photos') or []
    if compact:
        photos = photos[:1]
    if photos:
        single = ' single' if len(photos) == 1 else ''
        parts.append('  <div class="photos%s">' % single)
        for p in photos:
            cap = p.get('caption') or ''
            parts.append('    <figure>%s<figcaption>%s</figcaption></figure>'
                         % (_img_tag(str(p['src']), cap, meta), esc(cap)))
        parts.append('  </div>')

    for n in (d.get('notes') or []):
        if n['kind'] == 'summary':
            parts.append(_note_row('summary', n['text']))

    for s in (d.get('slots') or []):
        body, _ = _pick_section(s.get('body'), compact)
        parts.append('  <div class="slot">')
        parts.append('    <div class="slot-time">%s</div>' % esc(s['time']))
        parts.append('    <div class="slot-body">%s</div>' % body)
        parts.append('  </div>')

    tail = [n for n in (d.get('notes') or []) if n['kind'] != 'summary']
    if compact and tail:
        # 精简版把「今日交通汇总」收进 <details>：那是**出发前对一遍**的东西，
        # 而每个 slot 里已经逐段写了怎么走。用原生 <details> 而不是加 CSS——
        # 骨架样式一动，版式指纹（= 骨架 CSS 的 MD5）就变，全部示例的逐字节基线跟着失效。
        # 「今日吃 / 今晚住 / 警告」保留展开：那是当天随时要用的，折叠了反而要来回点。
        keep = [n for n in tail if n['kind'] in _NOTE_KEEP_IN_COMPACT]
        fold = [n for n in tail if n['kind'] not in _NOTE_KEEP_IN_COMPACT]
        for n in keep:
            parts.append(_note_row(n['kind'], n['text']))
        if fold:
            icons = ' '.join(NOTE_KINDS[n['kind']][0] for n in fold)
            parts.append('  <details><summary>%s 今日交通汇总（点开看）</summary>'
                         % icons)
            for n in fold:
                parts.append(_note_row(n['kind'], n['text']))
            parts.append('  </details>')
    else:
        for n in tail:
            parts.append(_note_row(n['kind'], n['text']))

    for w in (d.get('warnings') or []):
        parts.append('  <div class="warn-line">%s</div>' % w)

    parts.append('</div>')
    return '\n'.join('      ' + x for x in parts)


def _note_row(kind, text):
    icon, extra = NOTE_KINDS[kind]
    return ('  <div class="note-row%s"><span class="ic">%s</span><div>%s</div></div>'
            % (extra, icon, text))


def build_nav_days(days):
    out = []
    for i, d in enumerate(days, 1):
        out.append('      <a href="#day%d">第 %d 天 · %s</a>' % (i, i, esc(d['theme'])))
    return '\n'.join(out)


def build_nearby_block(nearby, compact=False):
    """把逐日的 `nearby`（沿途顺道可看、但没排进主线的点）渲染成一块，注入 `#drive`。

    与 `build_routes_blocks` 同一个理由：**注入既有区块、不新开 section**——
    骨架不动，版式指纹不变，既有示例的逐字节基线不破。
    没有 `nearby` 时返回 `None`，渲染结果与引入本功能前完全一致。

    ⚠️ 这里的每一处点都应当**核过开放日**再写进事实源。「顺道去看看」最怕的是
    到了门口才发现那天闭馆——本 skill 已经为此栽过一次（外滩美术馆周二不开）。
    """
    if not isinstance(nearby, list):
        return None
    items = [n for n in nearby
             if isinstance(n, dict) and str(n.get('name') or '').strip()]
    if not items:
        return None

    if compact:
        lines = []
        for it in items:
            where = []
            if it.get('near'):
                where.append('近%s' % it['near'])
            if it.get('distance'):
                where.append(str(it['distance']))
            tail = (str(it.get('brief') or '').strip()
                    or str(it.get('note') or '').strip()
                    or str(it.get('evidence') or '').strip())
            lines.append('<li><b>%s</b>%s —— %s</li>' % (
                esc(str(it['name'])),
                ('（%s）' % '、'.join(where)) if where else '',
                tail))
        return ('<div class="card" data-nearby="1" data-nearby-count="%d">'
                '<h3>沿途顺道可看（%d 处）</h3><ul>%s</ul></div>'
                % (len(items), len(items), ''.join(lines)))

    rows = []
    for it in items:
        tail = str(it.get('note') or '').strip()
        evidence = str(it.get('evidence') or '').strip()
        cells = [esc(str(it['name'])),
                 esc(str(it.get('near') or '—')),
                 esc(str(it.get('distance') or '—')),
                 (tail + ' ' + evidence).strip() or '—']
        rows.append('<tr>%s</tr>' % ''.join('<td>%s</td>' % c for c in cells))
    return ('<div class="card" data-nearby="1" data-nearby-count="%d">'
            '<h3>沿途顺道可看（%d 处）</h3>'
            '<table class="tb"><tr><th>点</th><th>哪一站附近</th><th>距离</th>'
            '<th>说明</th></tr>%s</table></div>'
            % (len(items), len(items), ''.join(rows)))


def build_routes_blocks(routes, compact=False):
    """顶层 `routes` → (主路线 HTML, 备选路线 HTML)。

    主路线注 `#drive`（讲怎么走），备选注 `#boost`（讲还能怎么走）。
    与 `build_intel_strip` 同一个理由：**注入既有区块、不新开 section**——
    加 section 得动基准骨架，骨架一变版式指纹就变，全部示例的逐字节回归基线跟着失效。

    没有 `routes` 字段时返回 `(None, None)`：**本功能对老事实源零影响**，
    渲染结果与引入前逐字节一致（三个既有示例的复现基线就是这么保住的）。
    """
    if not isinstance(routes, dict):
        return None, None

    def stops(items):
        names = [str(s).strip() for s in (items or []) if str(s).strip()]
        return ' → '.join(esc(n) for n in names)

    primary_html = None
    primary = routes.get('primary')
    if isinstance(primary, dict):
        name = str(primary.get('name') or '').strip()
        if name:
            parts = ['<div class="card" data-route="primary">',
                     '<h3>主路线 · %s</h3>' % esc(name)]
            if primary.get('summary'):
                parts.append('<p>%s</p>' % esc(primary['summary']))
            if stops(primary.get('stops')):
                parts.append('<p><b>途经</b>：%s</p>' % stops(primary.get('stops')))
            if primary.get('best_for'):
                parts.append('<p><b>什么情况走它</b>：%s</p>' % esc(primary['best_for']))
            parts.append('</div>')
            primary_html = ''.join(parts)

    alternatives_html = None
    alt = routes.get('alternatives')
    if isinstance(alt, list):
        items = [i for i in alt
                 if isinstance(i, dict) and str(i.get('name') or '').strip()]
        if items:
            if compact:
                # 精简版：换成行式列表，逐条只留「叫什么 + 什么时候走 + 关键数据」。
                # 删「途经」列属于**换写法**不是压措辞——执行件上路时路线已经在主线里标过；
                # 证据时戳与徽章标记仍逐条带在身边，不降标准。
                lines = []
                for it in items:
                    brief = str(it.get('brief') or '').strip()
                    if not brief:
                        bits = [str(it.get('best_for') or '').strip(),
                                str(it.get('tradeoff') or '').strip(),
                                str(it.get('evidence') or '').strip()]
                        brief = ' '.join(b for b in bits if b)
                    lines.append('<li><b>%s</b> —— %s</li>'
                                 % (esc(str(it['name'])), brief))
                alternatives_html = (
                    '<div class="card" data-route="alternatives" data-alt-count="%d">'
                    '<h3>备选路线（%d 条）</h3><ul>%s</ul></div>'
                    % (len(items), len(items), ''.join(lines)))
            else:
                rows = []
                for it in items:
                    detail = []
                    if it.get('tradeoff'):
                        detail.append(esc(str(it['tradeoff'])))
                    if it.get('evidence'):
                        # 证据句不 esc：要保留 [A] / [B] 标记，渲染器随后转成徽章
                        detail.append(str(it['evidence']))
                    cells = [esc(str(it['name'])), stops(it.get('stops')) or '—',
                             esc(str(it.get('best_for') or '—')),
                             ' '.join(detail) or '—']
                    rows.append('<tr>%s</tr>' % ''.join('<td>%s</td>' % c for c in cells))
                alternatives_html = (
                    '<div class="card" data-route="alternatives" data-alt-count="%d">'
                    '<h3>备选路线（%d 条）</h3>'
                    '<table class="tb"><tr><th>路线</th><th>途经</th><th>什么情况走它</th>'
                    '<th>代价 / 依据</th></tr>%s</table></div>'
                    % (len(items), len(items), ''.join(rows)))

    return primary_html, alternatives_html


def build_preferences_block(preferences):
    """把顶层 `preferences`（用户**自报**的饮食/作息/兴趣偏好）渲染成一小块，
    注入 `#drive`（主路线讲「怎么走」，这块讲「按你的习惯排的」）。

    与 `build_routes_blocks` 同一个理由：**注入既有区块、不新开 section**——
    骨架不动，版式指纹不变，老示例的逐字节基线不破。
    没有 `preferences` 或三键全空时返回 `None`，渲染结果与引入前完全一致。

    这是「个性化」的边界所在：只呈现**用户原话**（饮食忌口、作息、兴趣），
    **绝不呈现任何模型推断**——推断值会冒充采集值，违背「每个数字都有来源」。
    缺了某个维度就那一行不画，不编一条出来。
    """
    if not isinstance(preferences, dict):
        return None
    rows = []
    diet = preferences.get('diet')
    if diet:
        if isinstance(diet, list):
            diet = '、'.join(str(x).strip() for x in diet if str(x).strip())
        if diet:
            rows.append('<li><b>饮食</b>：%s</li>' % esc(str(diet)))
    pace = preferences.get('pace')
    if pace:
        if isinstance(pace, dict):
            bits = []
            if pace.get('wake_time'):
                bits.append('作息 ' + str(pace['wake_time']))
            if pace.get('nap'):
                bits.append('午休 ' + str(pace['nap']))
            if bits:
                rows.append('<li><b>节奏</b>：%s</li>' % esc('；'.join(bits)))
        elif str(pace).strip():
            rows.append('<li><b>节奏</b>：%s</li>' % esc(str(pace)))
    interests = preferences.get('interests')
    if interests:
        if isinstance(interests, list):
            interests = '、'.join(str(x).strip() for x in interests if str(x).strip())
        if interests:
            rows.append('<li><b>兴趣</b>：%s</li>' % esc(str(interests)))
    if not rows:
        return None
    return ('<div class="card" data-preferences="1">'
            '<h3>按你的偏好排的（你自报，未做推断）</h3><ul>%s</ul></div>'
            % ''.join(rows))


def build_intel_strip(si):
    """把 `meta.social_intel` 渲染成一条「情报来源」声明，注入 #overview 顶部。

    为什么塞进既有区块、而不是新加一个 section：加 section 得动基准骨架，
    骨架一变，版式指纹（= 骨架 CSS 的 MD5）就变，全部示例的逐字节回归基线跟着失效。
    这条声明属于内容层，注入既有区块零副作用。

    它同时是交付自查第 ㉗ 项的判据来源——**必须落在正文里，机器才 grep 得到**。
    声明有且只有两种形态，对应阶段 3.5 的两种出口：
      · 采到了 → 条数 + 走哪条路径 + 线索卡 + 核实日期
      · 没去采 → 明写「本次未采集社会情报」+ 理由（跳过可以，静默跳过不行）
    """
    if not isinstance(si, dict):
        return ''
    reason = str(si.get('skipped_reason') or '').strip()
    status = str(si.get('status') or '').strip().upper()
    if status in ('SKIPPED', 'NOT_COLLECTED', 'NONE') or (not status and reason):
        return ("<p class='lead'><b>情报来源</b>：本次未采集社会情报——%s。"
                "死线不变：票价 / 营业时间 / 车次余票 / 距离车程从不取自社媒，"
                "这部分始终回官方渠道与地图核。</p>" % (esc(reason) or '原因见下方说明'))

    paths = si.get('paths') or []
    if isinstance(paths, str):
        paths = [paths]
    bits = []
    if si.get('note_count'):
        bits.append('已采集 <b>%s 条</b>' % esc(si['note_count']))
    if paths:
        bits.append('路径：%s' % ' / '.join(esc(str(p)) for p in paths))
    if si.get('notes_file'):
        bits.append('线索卡 <b>%s</b>' % esc(si['notes_file']))
    if si.get('checked_at'):
        bits.append('核实于 %s' % esc(si['checked_at']))
    head = '；'.join(bits) if bits else '已采集'

    # 口碑构成（可选）：谁在说——机构口径与真人评价各占多少。
    # 刻意做成「分布」而不是「好评率」：只有机构稿时，好评率毫无意义。
    # 这条同时是「不得把机构口径包装成民意」的机械提醒。
    voice_note = ''
    vd = si.get('voices')
    if isinstance(vd, dict) and vd:
        order = ['OFFICIAL', 'MEDIA', 'EDITOR', 'CREATOR',
                 'TRAVELER', 'LOCAL', 'COMMENT', 'UNKNOWN']
        label = {'OFFICIAL': '官方机构', 'MEDIA': '媒体', 'EDITOR': '攻略编辑',
                 'CREATOR': '内容创作者', 'TRAVELER': '游客自述', 'LOCAL': '本地人',
                 'COMMENT': '评论区', 'UNKNOWN': '身份未判'}
        parts = ['%s %s' % (label.get(k, k), vd[k]) for k in order if vd.get(k)]
        if parts:
            voice_note = '<br>口碑构成：%s。' % ' / '.join(parts)
            if not any(vd.get(k) for k in ('TRAVELER', 'LOCAL', 'COMMENT')):
                voice_note += ('<b>本趟未采到真人评价与评论</b>——'
                               '文中不得表述为「游客普遍反映」。')

    # 口碑体检摘要（可选）：meta.social_intel.review_trust —— review_trust.py 的
    # 产出手填进来。纪律同口碑层：信号不是结论，只报数量与用法，不做好评率、
    # 不产「调整后评分」。缺字段时一行不加，老事实源逐字节不变。
    rt = si.get('review_trust')
    if isinstance(rt, dict) and rt.get('reviews_total'):
        rt_bits = ['评论 %s 条' % esc(str(rt['reviews_total']))]
        if rt.get('water_suspects'):
            rt_bits.append('可疑 %s' % esc(str(rt['water_suspects'])))
        if rt.get('promo_hits'):
            rt_bits.append('含推广标记 %s' % len(rt['promo_hits']))
        if rt.get('repeat_phrases'):
            rt_bits.append('复读指纹 %s' % len(rt['repeat_phrases']))
        voice_note += ('<br>口碑体检：%s——<b>信号不是结论</b>，'
                       '命中项按「排后面看」处理。' % '；'.join(rt_bits))

    return ("<p class='lead'><b>情报来源</b>：%s。%s死线不变：票价 / 营业时间 / "
            "车次余票 / 距离车程从不取自社媒，这部分始终回官方渠道与地图核。</p>"
            % (head, voice_note))


def build_bulletin_strip(mb):
    """把 `meta.bulletin` 渲染成「行前公告」条，注入 #overview 顶部。

    与情报来源条同款纪律：塞进既有区块、不新开 section（动骨架会炸逐字节
    基线）。bulletin.py --check 的 render_summary 手填进来；没有实质条目时
    返回空串——老事实源的渲染结果逐字节不变。

    推荐联动只提示不改行程：「XX 闭馆——当日行程建议核对备选」是给读者的
    提醒，行程怎么调由人决定。
    """
    if not isinstance(mb, dict):
        return ''
    items = [it for it in (mb.get('items') or []) if isinstance(it, dict)]
    if not items:
        return ''
    marks = {'CLOSURE': '闭馆', 'PRICE': '调价', 'CONTROL': '管制',
             'OPENING': '新开', 'EVENT': '活动'}
    checked = str(mb.get('checked_at') or '').strip()
    lines = []
    for it in items:
        label = marks.get(str(it.get('type') or ''), str(it.get('type') or ''))
        line = '· <b>%s</b>（%s）：%s' % (
            esc(str(it.get('poi') or '—')), esc(label),
            esc(str(it.get('text') or ''))[:60])
        hint = str(it.get('hint') or '').strip()
        if hint:
            line += '——%s' % esc(hint)
        lines.append(line)
    head = "<p class='lead' data-bulletin='1'><b>行前公告</b>%s：" % (
        '（官方渠道核实于 %s）' % esc(checked) if checked else '')
    return head + '<br>' + '<br>'.join(lines) + '</p>'


# ============================ 结构钩子 ============================
def hook_check_inputs(s):
    """补齐 .check 列表里 li 的 <input>——骨架 JS 会对 li.querySelector("input").id
    取值，缺 input 直接抛错、勾选记忆全部失效。

    ⚠️ 作用域必须限定在 .check 列表内：给普通 <ul><li> 也塞 input 会在页面上
    多出一排空复选框。属性引号单双都要认（模型产出的片段不一定用哪种）。"""
    counter = [0]

    def fix_li(m):
        block = m.group(0)
        if '<input' in block:
            return block
        counter[0] += 1
        return block.replace('>', '><input type="checkbox" id="ck%d">' % counter[0], 1)

    def fix_list(m):
        return m.group(1) + re.sub(r'<li[^>]*>.*?</li>', fix_li, m.group(2), flags=re.S) + m.group(3)

    s = re.sub(r'(<[ou]l[^>]*class=["\'][^"\']*\bcheck\b[^"\']*["\'][^>]*>)(.*?)(</[ou]l>)',
               fix_list, s, flags=re.S)
    return s, counter[0]


def hook_tip_wrap(s):
    """补齐 .tips .tip 的包裹层——骨架注释实证：裸文本会被 ::before 的 44px
    序号列挤成竖排（武汉 v1 事故）。

    判据：.tip 内没有任何元素子节点才包一层。.tip 的正文本身不含嵌套 div，
    所以「到第一个 </div>」就是可靠边界。"""
    counter = [0]

    def fix(m):
        inner = m.group(2)
        if re.search(r'<\w', inner):
            return m.group(0)
        counter[0] += 1
        return '%s<div>%s</div>%s' % (m.group(1), inner, m.group(3))

    s = re.sub(r'(<div[^>]*class=["\'][^"\']*\btip\b[^"\']*["\'][^>]*>)(.*?)(</div>)',
               fix, s, flags=re.S)
    return s, counter[0]


# 证据等级：徽章上的字母 → title 里读得到的全称
_EV_LABELS = {'A': '工具实证', 'B': '多源交叉', 'C': '单源线索', 'D': '未核实'}
# 行内标记：[A] / [B] / [C] / [D]，以及带附加说明的 [C·单源] / [C·单源·小红书2026-09帖]
_EV_RE = re.compile(r'\[([ABCD])((?:·[^\[\]]{1,24})*)\]')


def hook_evidence_badges(s):
    """把正文里的行内证据标记转成徽章（规格见 references/evidence-rules.md）。

    标记是**写在正文里**的，所以这里只做渲染，不需要给事实源加字段——
    「每个数字有来源」的落地方式是「句子里挂一枚等级标」，不是另建一张表。

    ⚠️ 必须避让标签内部与 script/style 块：先整块 stash，替换完再放回。
    否则 `<span title="等级[A]">` 会被误伤，JS 里的 `arr[B]` 这类下标也会被吞掉。
    """
    stash = []

    def _hold(m):
        stash.append(m.group(0))
        return '\x00%d\x00' % (len(stash) - 1)

    # ① script / style 整块保护（内部不是正文）
    s = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', _hold, s, flags=re.S | re.I)
    # ② 其余标签本身保护（属性里可能带方括号）
    s = re.sub(r'<[^>]+>', _hold, s)

    counter = {k: 0 for k in 'ABCD'}

    def fix(m):
        lvl, extra = m.group(1), m.group(2) or ''
        counter[lvl] += 1
        note = ''
        if extra:
            # extra 形如 `·单源·小红书2026-09帖`，去掉首个中点后作小字挂在徽章后
            note = '<span class="ev-note">%s</span>' % esc(extra[1:])
        return '<span class="ev ev-%s" title="证据等级 %s·%s">%s</span>%s' % (
            lvl.lower(), lvl, _EV_LABELS[lvl], lvl, note)

    s = _EV_RE.sub(fix, s)
    s = re.sub(r'\x00(\d+)\x00', lambda m: stash[int(m.group(1))], s)
    return s, counter


def _pick_section(text, compact):
    """从 `sections.<k>`（或 `days[].slots[].body`）取当前版本该用的文案，
    返回 (文案, 是否显式提供了该档)。

    支持两种写法：
      · 字符串                     → 两版共用（省事，但精简版不会因此变短）
      · `{"full": …, "compact": …}` → 各版取各的

    **两档写在同一个字段里，是刻意的**：分成两个字段（或两份文件）必然出现
    「改了这边忘了那边」，而写在同一处，改的时候躲不开另一档。
    这也让「哪些部分还没写精简文案」可以机械数出来（`--compact` 渲染时会打印清单，
    `tools/compact_check.py` 会据此判 FAIL）。

    ⚠️ 两档不是「压缩措辞」而是**换写法**：`compact` 要删掉评价/典故/理由，
    只留「几点 / 去哪（含门牌）/ 怎么去 / 花多少 / 什么状态 / 什么强制约束」。
    证据徽章与来源时戳**一条都不能少**——短不是降标准的理由。
    """
    if isinstance(text, dict):
        key = 'compact' if compact else 'full'
        picked = str(text.get(key) or text.get('full') or '').strip()
        return picked, bool(str(text.get(key) or '').strip())
    return str(text or '').strip(), False


def build_quickview(days):
    """精简版专属：把 N 天压成一张表，注入 #overview 顶部。

    它解决的是「看到一半忘了前文」——**把全局锚点放在第一屏**，
    读者任何时候翻回来都能一眼定位「我正在第几天、这一天图什么」。
    数据全部来自 days（date / theme / summary），零额外维护。
    """
    rows = []
    for i, d in enumerate(days, 1):
        summary = ''
        for n in (d.get('notes') or []):
            if n.get('kind') == 'summary':
                summary = re.sub(r'<[^>]+>', '', str(n.get('text') or '')).strip()
                break
        if len(summary) > 56:
            summary = summary[:56] + '…'
        # 「第 N 天」做成锚点链接：读者不用滚动去找，点一下就到当天卡片。
        # 这是解决「看到一半忘了前文」最省事也最有效的一招——回得来，就不怕往下走。
        rows.append("<tr><td><a href='#day%d'>第 %d 天</a></td><td>%s</td>"
                    "<td>%s</td><td>%s</td></tr>"
                    % (i, i, esc(str(d.get('date') or '')), esc(d.get('theme') or ''),
                       esc(summary)))
    return ("<div class='card'><p><b>这是精简执行版</b>——只留要动手做的事。"
            "下面这张表是<b>全局定位</b>：点「第 N 天」直接跳到当天。"
            "本页没有的东西（住宿比价、预算逐项、备选点、特产细节）都在<b>完整版</b>里。</p>"
            "<table class='tb'><thead><tr><th>天</th><th>日期</th><th>主线</th>"
            "<th>一句话</th></tr></thead><tbody>%s</tbody></table></div>"
            % ''.join(rows))


#: 精简版把哪些区块收进 <details>。
#:
#: ⚠️ 2026-09-27 改版：**本集合已清空**。
#:
#: 原设计把 5 个「资料型」板块（备选 / 住宿 / 预算 / 美食全表 / 特产）收进 `<details>`，
#: 但实测只让全篇省了 15%——`<details>` 只是把内容**移出首屏**，字数与 DOM 长度一分未减。
#: 而且它与本文件里 `build_quickview` 对读者的承诺自相矛盾：那里写「本页没有的东西
#: （住宿比价、预算逐项、备选点、特产细节）都在完整版里」——**声明与实现必须一致**。
#:
#: 现在的做法：这些板块的 `compact` 档**不写正文**，只留一行「本版不展开，见完整版」
#: 的指引（由 `_pick_section` 取档，无需特殊 HTML）。集合保留为空，是为了将来
#: 真要折叠某个板块时仍有挂点。
_FOLD_IN_COMPACT = {}


def _fold_section(frag, label):
    """把区块正文收进 <details>，标题留在外面——读者看得到有什么，点开才有内容。"""
    cut = frag.find('</h2>')
    if cut < 0:
        return "<details><summary>%s（点开看）</summary>%s</details>" % (label, frag)
    head, rest = frag[:cut + 5], frag[cut + 5:]
    return "%s<details><summary>%s —— 点开看</summary>%s</details>" % (head, label, rest)


# ============================ 主渲染 ============================
def render(base, data, quiet=False, compact=False):
    days, secs, meta = validate(data)
    s = base
    log = []

    n_days = len(days)
    first_date = str(days[0]['date'])

    # ---- ⓿ 硬闸：基准骨架里的每个占位符都必须有归属
    # 骨架一旦新增占位符而事实源不认识，这里直接拒绝渲染——不许悄悄漏到成品里。
    known = set(PLACEHOLDER_MAP) | DYNAMIC_PLACEHOLDERS
    unknown = sorted(set(re.findall(r'【[^】]+】', base)) - known)
    if unknown:
        raise DataError(
            '基准骨架出现未登记占位符（渲染器不知道该填什么，拒绝产出半成品）：\n'
            '   - %s\n'
            '   处置：在 tools/render_html.py 的 PLACEHOLDER_MAP 中登记，或改事实源写法。'
            % '\n   - '.join(unknown))

    # ---- ① 10 类基准占位符
    for ph, field in PLACEHOLDER_MAP.items():
        val = str(meta.get(field) or '').strip()
        if not val:
            raise DataError('占位符 %s 无对应值（meta.%s）' % (ph, field))
        s = s.replace(ph, esc(val))
    # 给 meta 派生缺省项
    if not meta.get('title'):
        meta['title'] = '%s路书' % meta['destination']

    # ---- ② 城市码
    if '__CITY__' in s:
        s = s.replace('__CITY__', meta['city_code'])
        log.append('城市码 __CITY__ → %s' % meta['city_code'])

    # ---- ③ TRIP 日期映射
    trip = ', '.join('"%s":"day%d"' % (str(d['date']), i) for i, d in enumerate(days, 1))
    s, n = re.subn(r'var TRIP = \{[^}]*\};', 'var TRIP = { %s };' % trip, s)
    if n != 1:
        raise DataError('未能在骨架中定位 TRIP 对象（骨架版本可能已变）')
    # 当日模式的「行程开始前」基准日
    s, n = re.subn(r'(todayStr\(\)\s*<\s*)"[\d-]+"', r'\1"%s"' % first_date, s)
    if n != 1:
        raise DataError('未能在骨架中定位当日模式的基准日')
    log.append('TRIP 映射 %d 天，基准日 %s' % (n_days, first_date))

    # ---- ④ 侧栏 day 导航
    s, n = re.subn(r'(?:[ \t]*<a href="#day\d+">[^<]*</a>\n?)+',
                   build_nav_days(days) + '\n', s)
    if n != 1:
        raise DataError('未能在骨架中定位侧栏 day 导航')

    # ---- ⑤ day 区块随天数增删（整体重排）
    day_spans = list(re.finditer(r'[ \t]*<section id="day\d+">.*?</section>\n?', s, re.S))
    if not day_spans:
        raise DataError('未能在骨架中定位 day 区块')
    start, end = day_spans[0].start(), day_spans[-1].end()
    new_sections = []
    for i, d in enumerate(days, 1):
        new_sections.append('        <section id="day%d">\n%s\n        </section>'
                            % (i, build_day_block(i, d, meta, compact=compact)))
    s = s[:start] + '\n'.join(new_sections) + '\n' + s[end:]
    if n_days != len(day_spans):
        log.append('day 区块 %d → %d' % (len(day_spans), n_days))

    # ---- ⑤.4 版本化文案：sections 支持 full / compact 双档
    # 必须在注入之前做完——后面几步（情报条、速览）都是往 overview 前面拼东西，
    # 拼在 dict 上会得到一串 repr。
    missing_compact = []
    picked = {}
    for key, frag in secs.items():
        body, explicit = _pick_section(frag, compact)
        picked[key] = body
        if compact and isinstance(frag, dict) and not explicit and key != 'intel':
            missing_compact.append(key)
    secs = picked

    # ⑤.4b 逐日 slot 的双档覆盖率：days 占全篇约 45%，`sections` 写短了但 slot 没写，
    # 精简版照样瘦不下来（中山实测：只写 sections 时全篇只省 15%）。
    if compact:
        n_all = sum(len(d.get('slots') or []) for d in days)
        n_dual = sum(1 for d in days for sl in (d.get('slots') or [])
                     if isinstance(sl.get('body'), dict))
        if n_dual < n_all:
            missing_compact.append('slots %d/%d' % (n_dual, n_all))

    # ---- ⑤.5 情报来源声明：meta.social_intel → #overview 顶部
    # 社会情报采集是阶段 3.5 的默认动作，声明必须落进正文，第 ㉗ 项才 grep 得到。
    _strip = build_intel_strip(meta.get('social_intel'))
    if _strip:
        secs['overview'] = _strip + '\n' + (secs.get('overview') or '')
        log.append('情报来源声明已注入 #overview（交付自查第 ㉗ 项的判据）')

    # ---- ⑤.5a 行前公告：meta.bulletin → #overview（bulletin.py --check 的产出）
    # 同款注入纪律：不新开 section；无条目返回空串，老事实源逐字节不变。
    _bull = build_bulletin_strip(meta.get('bulletin'))
    if _bull:
        secs['overview'] = _bull + '\n' + (secs.get('overview') or '')
        log.append('行前公告已注入 #overview（meta.bulletin，data-bulletin 可 grep）')

    # ---- ⑤.5b 路线结构：顶层 routes → 注入既有区块（不新开 section，理由同上）
    # 主路线进 #drive（讲怎么走），备选路线进 #boost（讲还能怎么走）。
    # 没有 routes 字段时两处都不动——老事实源的渲染结果逐字节不变。
    _rp, _ra = build_routes_blocks(data.get('routes'), compact)
    if _rp:
        secs['drive'] = (secs.get('drive') or '') + '\n' + _rp
        log.append('主路线已注入 #drive（顶层 routes.primary）')
    if _ra:
        secs['boost'] = _ra + '\n' + (secs.get('boost') or '')
        log.append('备选路线已注入 #boost（顶层 routes.alternatives）')

    # ---- ⑤.5c 沿途顺道点：days[].nearby → 注入 #drive（同样不新开 section）
    _nb_raw = [it for d in days for it in (d.get('nearby') or [])
               if isinstance(it, dict)]
    _nb = build_nearby_block(_nb_raw, compact)
    if _nb:
        secs['drive'] = (secs.get('drive') or '') + '\n' + _nb
        log.append('沿途顺道点已注入 #drive（days[].nearby，%d 处）' % len(_nb_raw))

    # ---- ⑤.5d 个性化偏好：顶层 preferences → 注入 #drive（用户自报，非推断）
    _pref = build_preferences_block(data.get('preferences'))
    if _pref:
        secs['drive'] = (secs.get('drive') or '') + '\n' + _pref
        log.append('个性化偏好已注入 #drive（顶层 preferences）')

    # ---- ⑤.6 精简版：首屏全局锚点
    # 「看到一半忘了前文」的解药不是删内容，是**把全局锚点放到第一屏**。
    if compact:
        secs['overview'] = build_quickview(days) + '\n' + (secs.get('overview') or '')
        log.append('精简版：已注入「一眼看全 %d 天」速览（首屏全局锚点）' % n_days)
        log.append('精简版：每天配图收敛为 1 张（footer 与 title 已标「精简版」）')
        if missing_compact:
            log.append('⚠️ 精简版：以下板块未提供 compact 文案，本版沿用了完整文案——%s'
                       % '、'.join(sorted(missing_compact)))

    # ---- ⑥ 其余区块内容注入
    folded = []
    for key, frag in secs.items():
        if key == 'intel':
            continue
        body = frag.strip()
        if compact and key in _FOLD_IN_COMPACT:
            body = _fold_section(body, _FOLD_IN_COMPACT[key])
            folded.append(key)
        pat = re.compile(r'(<section id="%s">).*?(</section>)' % re.escape(key), re.S)
        if not pat.search(s):
            raise DataError('骨架中没有区块 #%s，无法注入' % key)
        s = pat.sub(lambda m: m.group(1) + '\n' + body + '\n        ' + m.group(2), s, count=1)
    if folded:
        log.append('精简版：%d 个资料型区块已收进折叠（内容未删，只是不占首屏）——%s'
                   % (len(folded), '、'.join(sorted(folded))))

    # ---- ⑦ 变体：friend 版删 #intel（单 <style>）
    keep_intel = bool(meta.get('keep_intel')) or meta.get('variant') == 'self'
    if not keep_intel:
        s, n = re.subn(r'[ \t]*<!-- 探店情报原档.*?-->\n?[ \t]*<section id="intel">.*?</section>\n?',
                       '', s, flags=re.S)
        if n == 0:
            s, n = re.subn(r'[ \t]*<section id="intel">.*?</section>\n?', '', s, flags=re.S)
        if n:
            log.append('friend 版：已移除 #intel（保持单 <style>）')
    else:
        log.append('self 版：保留 #intel（须自带第 2 块 <style>）')

    # ---- ⑧ 结构钩子
    s, n1 = hook_check_inputs(s)
    if n1:
        log.append('钩子：为 %d 个 .check li 补 <input>' % n1)
    s, n2 = hook_tip_wrap(s)
    if n2:
        log.append('钩子：为 %d 条 .tip 补包裹层（防序号列挤竖排）' % n2)
    s, n3 = hook_evidence_badges(s)
    if sum(n3.values()):
        log.append('证据徽章：A%d / B%d / C%d / D%d'
                   % (n3['A'], n3['B'], n3['C'], n3['D']))
    else:
        log.append('证据徽章：0 枚——正文里没有 [A]/[B]/[C]/[D] 标记')

    # ---- ⑨ title 与 generator 署名
    # 精简版必须**在标题与页脚都标明**——两个版本会同时存在手机里，
    # 打开时必须一眼知道自己在看哪一版，否则会拿精简版当完整版查资料。
    variant_tag = '（精简版）' if compact else ''
    s = re.sub(r'<title>[^<]*</title>', '<title>%s · %s%s</title>'
               % (esc(meta['title']), esc(meta['destination']), variant_tag), s, count=1)
    s = s.replace('路书基准骨架 v1 · 指纹',
                  '路书基准骨架 v1（源：jianhao-travel-planner, MIT）· 指纹'
                  + ('　·　本页为精简执行版' if compact else ''), 1)

    # ---- ⑩ 终检一：产物不得残留任何基准占位符（无长度上限）
    leftover = sorted(set(re.findall(r'【[^】]+】', s)) & known)
    if leftover:
        raise DataError('渲染后仍残留基准占位符：%s' % leftover)
    if '__CITY__' in s:
        raise DataError('渲染后仍残留 __CITY__')

    # ---- ⑪ 终检二：每个区块必须真有内容
    # 上游踩过的坑：CSS 一直在、内容从没生成，光看样式或跑版式校验都发现不了。
    empty = []
    for m in re.finditer(r'<section id="([^"]+)">(.*?)</section>', s, re.S):
        body = re.sub(r'<!--.*?-->', '', m.group(2), flags=re.S)
        text = re.sub(r'\s+', '', re.sub(r'<[^>]+>', '', body))
        if len(text) < 8:
            empty.append(m.group(1))
    if empty:
        raise DataError('以下区块渲染后为空（骨架在、内容缺）：%s' % empty)

    if meta.get('_inlined'):
        log.append('配图内联 %d 张（%.1f KB）——本地单文件版，断网可开'
                   % (meta['_inlined'], meta['_inlined_bytes'] / 1024.0))

    if not quiet:
        emit('渲染完成：%d 天 · %d 区块 · 起 %s' % (n_days, len(re.findall(r'<section id=', s)), first_date))
        for x in log:
            emit('   · ' + x)
    return s


def main():
    ap = argparse.ArgumentParser(description='路书渲染器：ludbook.json → 路书.html（数据与模板分离）')
    ap.add_argument('source', help='事实源 JSON')
    ap.add_argument('-o', '--out', help='输出 HTML（默认与源同名 .html）')
    ap.add_argument('--base', default=DEFAULT_BASE, help='基准骨架 HTML')
    ap.add_argument('--no-inline', action='store_true',
                    help='不内联本地图（生成瘦身在线版，图走远端）')
    ap.add_argument('--compact', action='store_true',
                    help='渲染精简版：sections 取 compact 档文案 + 首屏「一眼看全 N 天」'
                         '+ 每天只留 1 张配图（骨架、指纹、30 项自查口径完全一致）')
    ap.add_argument('--verify', action='store_true', help='渲染后立即跑 consistency.py 一致性校验')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()

    base = read(a.base)
    try:
        data = json.loads(read(a.source))
    except json.JSONDecodeError as e:
        emit('FATAL 事实源不是合法 JSON：%s' % e)
        sys.exit(1)

    # 内部字段：配图相对路径的基准目录 + 是否内联
    if isinstance(data, dict):
        data.setdefault('meta', {})
        if isinstance(data['meta'], dict):
            data['meta']['_base_dir'] = os.path.dirname(os.path.abspath(a.source))
            data['meta']['_no_inline'] = bool(a.no_inline)

    try:
        out = render(base, data, quiet=a.quiet, compact=a.compact)
    except DataError as e:
        emit('❌ %s' % e)
        sys.exit(1)

    dest = a.out or os.path.splitext(a.source)[0] + '.html'
    io.open(dest, 'w', encoding='utf-8', newline='\n').write(out)
    emit('✅ 已写出 %s（%.1f KB）' % (os.path.basename(dest), len(out.encode('utf-8')) / 1024))

    if a.verify:
        # 走一致性校验：本进程内联，避免额外子进程
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        emit('\n' + '-' * 78)
        import consistency
        sys.argv = ['consistency.py', dest, '--base', a.base, '--days', '0']
        data_days = len(data.get('days') or [])
        sys.argv[-1] = str(data_days)
        consistency.main()


if __name__ == '__main__':
    main()
