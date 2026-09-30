#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
social_notes.py — 社媒 / 网页线索归一化 + 死线校验

为什么需要它
------------
`references/data-contracts.md` 早就定义了 Social Research Result 的结构，
`compile-research` 也能把归一化后的笔记汇编成景点卡。**中间那一步一直没人做**——
把「搜到的东西」变成那个结构。于是社媒线索要么进不了管线，要么以散文形式绕过去，
绕过去就等于把证据规则也绕过去了。

本文件补这一步，并且顺手把三条死线做成机器判定（判不了的就是没落地）：

  ① **证据类别只有六种**（ROUTE_HYPOTHESIS / TRAVEL_TIME_HINT / PRICE_SIGNAL /
     EXPERIENCE / SEASONAL / QUEUE / CLOSURE）——自创类别直接拒。
     类别决定这条线索能走多远，自创一个就等于绕过了那张表。
  ② **价格只能以 PRICE_SIGNAL 出现**，且必须标未核实。写进别的类别 = 试图
     把一条笔记变成票价，直接拒。
  ③ **营业时间 / 车次时刻 / 余票没有任何类别可以承载**——出现即拒。
     这三类是 `[A]` 专属：回场馆、高德或 12306，不进社区笔记。

实测背景（2026-09-27，本机）
----------------------------
**直连社媒平台不可行，而且不该绕。** 无头 Edge 匿名访问：

| 目标 | 结果 |
|---|---|
| 小红书（首页 / 搜索页 / 笔记页） | `安全限制 · IP存在风险，请切换可靠网络环境后重试 · 300012` |
| 抖音（搜索页） | `验证码中间页` |

两者都是**风控拦截**，不是登录墙——所以「登录一下就能读」是错的判断。
按铁律 8：遇风控**一次即停手换路径**，不重试、不绕。

可行路径是**搜索引擎索引**与**官方文旅发布**：实测能拿到完整的玩法描述、
线路串联（如中山市旅游协会发布的「十大热门景区 + 八条精品线路」）。
本工具接受这三类来源，并要求用 `connector_type` 如实区分——不自报来源的
「据网友说」正是这个 skill 最不能接受的东西。

**一次采集、多趟复用**
----------------------
线索卡不是一次性的。同一目的地（或邻近目的地）下次再来，应当先读旧卡、
只补新增，而不是从零重搜——否则「持续积累」只是句口号。`--merge` 就是这一步的
机器实现：按 `url` 去重（无 url 退化为标题），本次重采到的用新的，旧的留作
`carried_over` 并按时效标 `stale`。**旧条目不会因为「这次没搜到」就消失**，
但也绝不会冒充新鲜情报——超期的会被点名。

用法
----
    python social_notes.py --input 线索.json                      # 校验 + 归一化
    python social_notes.py --input 线索.json --brief              # 顺带汇编成景点卡
    python social_notes.py --input 线索.json --json               # 结构化输出
    python social_notes.py --input 线索.json -o 归一化.json        # 落盘
    python social_notes.py --input 新线索.json --merge 旧线索卡.json  # 增量并入（去重+标记时效）
    python social_notes.py --input 旧线索卡.json --json            # 复用：先读旧卡看还差什么

退出码：`0` 通过 ｜ `1` 执行出错 ｜ `2` 校验未通过（有 error）
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

TOOLS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = TOOLS_DIR.parent
ENGINE_DIR = SKILL_ROOT / 'engine'
sys.path.insert(0, str(ENGINE_DIR))

try:
    from travel_planner.research import compile_destination_brief
except ImportError as exc:  # pragma: no cover - 只在引擎缺失时触发
    compile_destination_brief = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


#: 契约里定死的六种证据类别。多一个都不行——类别是「这条线索能走多远」的开关。
CLAIM_TYPES = frozenset({
    'ROUTE_HYPOTHESIS',
    'TRAVEL_TIME_HINT',
    'PRICE_SIGNAL',
    'EXPERIENCE',
    'SEASONAL',
    'QUEUE',
    'CLOSURE',
})

#: 连字符写法也接受（契约里 QUEUE / CLOSURE 是拿斜杠并列的两项）。
_CLAIM_ALIASES = {'QUEUE_CLOSURE': 'QUEUE'}

#: 笔记来源类型 → 建议证据等级。官方发布可到 [B]（官方渠道 + 媒体转载互证）；
#: UGC 与搜索索引一律 [C·单源]——**索引到的摘要不是第二来源**。
_LEVEL_BY_CONNECTOR = {
    'web_search_fallback': 'C',
    'browser': 'C',
    'user_provided': 'C',
}

#: 平台/机构名里出现这些词 → 视为官方发布（可升 [B]）。
_OFFICIAL_HINTS = (
    '文旅', '旅游局', '旅游协会', '人民政府', '政府', '官方', '妇联',
    '发布', '日报', '晚报', '新闻网',
)

# 死线正则
_PRICE_RE = re.compile(r'票价|门票价|门票|人均\s*\d|多少钱|价格|¥|￥|\d+\s*元')
#: 评价/评论专用的**具体金额**判据。刻意比 `_PRICE_RE` 窄：
#: 「价格还行」是感受，「人均 80 元」才是价格断言。宽判据在评论区会产出大量噪声，
#: 而**噪声大的闸门一定会被绕过**——所以这里只逮真金额。
_PRICE_AMOUNT_RE = re.compile(r'¥|￥|\d+\s*元|人均\s*\d|门票\s*\d|票价\s*\d')
#: 营业时间。「几点开放/闭馆」这类带时刻的说法一并拦——它们是「什么时候关」
#: 的断言，同样只能回官方或地图核。
#:
#: 刻意**不**拦「闭馆」两个字单独出现（如「周一闭馆」）：契约把 `CLOSURE`
#: 列为可用类别，那是当天状态而非营业时间表。带时刻才算越线。
#: 也不含「开始/结束」——「10:00 开始游览」是行程安排，不是营业时间。
_HOURS_RE = re.compile(
    r'营业时间|开放时间|开门时间|关门时间|开馆时间|闭馆时间|几点开|几点关|营业到'
    r'|\d{1,2}[:：]\d{2}\s*[-–~至]\s*\d{1,2}[:：]\d{2}'
    r'|\d{1,2}\s*[:：点]\s*\d{0,2}\s*(?:开放|开门|开馆|营业|闭馆|关门)'
)
_RAIL_RE = re.compile(r'车次|余票|列车时刻|航班时刻|发车时间|起飞时间|末班车|首班车')

#: 来源必须说明自己是怎么拿到的。自报来源是这套规则的起点。
_CONNECTOR_TYPES = frozenset({
    'official_api', 'community_mcp', 'browser', 'web_search_fallback', 'user_provided',
})

# ============================ 口碑层（谁在说 · 说了什么 · 可不可信） ============================
#
# 「多来源、多角度」不是靠采得更多，而是靠**分清谁在说**。同一句话，
# 文旅局说的、攻略站编辑写的、博主拍的、游客评论里留的，分量完全不同。
# 而「博主可能夸大」「评论区有水军」这两个担心，机器**判不了结论**，
# 只能给**信号**——所以下面所有输出都带 disclaimer，宁可漏报不可错判。

#: 说话人分型。决定了这条说法在「口碑」里的权重，也决定了要不要防夸大。
VOICE_KINDS = {
    'OFFICIAL': '官方机构（文旅局 / 政府 / 协会）',
    'MEDIA': '媒体机构（日报 / 新闻网）',
    'EDITOR': '平台编辑 / 攻略站（携程攻略编辑部一类）',
    'CREATOR': '内容创作者（博主 / UP 主——**夸大风险最高的一类**）',
    'TRAVELER': '普通游客（正文自述）',
    'LOCAL': '本地人（自称）',
    'COMMENT': '评论区（他人对该内容的评价）',
    'UNKNOWN': '无法判断——按最保守处理',
}

#: 评论/评价的立场。机器判不了语义，只能靠这三个值如实登记。
STANCES = ('POSITIVE', 'NEGATIVE', 'MIXED', 'NEUTRAL')

#: 创作者身份的识别词（出现在平台名或作者名里）。
_CREATOR_HINTS = ('小红书', '抖音', 'B站', 'bilibili', '微博', '马蜂窝',
                  'UP主', '博主', 'vlog', 'Vlog', '视频', '笔记')
_EDITOR_HINTS = ('攻略', '游记', '指南', '榜单', '编辑部', '专栏')
_MEDIA_HINTS = ('日报', '晚报', '新闻网', '电视台', '广播', '报业', '通讯社', '新闻')
_LOCAL_HINTS = ('本地人', '土著', '老街坊', '本地')

#: 推广/夸大词。**命中不等于夸大**——它只是一个「这条要多留个心眼」的信号，
#: 因为真正的软广和不带软广的真心推荐，在词面上是分不出来的。
_PROMO_WORDS = (
    '绝美', '天花板', '必去', '必吃', '必打卡', '必看', '全网最', '世界第一',
    '人生必去', '此生必去', '封神', '吊打', '不输', '宝藏', '秘境', '天花板级',
    '秒出片', '出片神器', '一天玩遍', '一天打卡', '保姆级', '内幕', '独家',
    '限时', '绝绝子', 'yyds', '爆火', '超值', '零差评', '好评如潮',
    '小众秘境', '人少景美', '私藏', '后悔没早来', '来了就不想走', '打开新世界',
)

#: 具体性证据：出现任一即认为这条评价「说得具体」。
#: 有细节的差评比没细节的好评值钱——这条正则是用来区分两者的。
_DETAIL_RE = re.compile(
    r'\d|元|号|路|街|巷|村|镇|市|区|馆|园|站|票|分钟|小时|公里|米'
    r'|早上|中午|下午|晚上|排队|停车|厕所|公交|地铁|打车|导航|门票|厕所')

#: 空洞夸赞（不含任何具体信息时，才构成「水军信号之一」）。
_GENERIC_PRAISE = (
    '不错', '很好', '好看', '好玩', '值得', '推荐', '喜欢', '太美了', '真好看',
    '很好玩', '非常棒', '很赞', '美', '好', '赞', '可以', '舒服', '惬意',
)

#: 批量账号特征（**启发式信号，不是结论**）：user+数字、4 位以上连续数字后缀、
#: 默认昵称一类。真实用户也可能长这样，所以它只作为组合判据的一项。
_WATER_ACCOUNT_RE = re.compile(
    r'(?i)^(user|用户|匿名|游客|momo|m0m0|小可爱)?[\s_\-]*\d{4,}$|^[a-z]{6,}\d{2,}$')

#: 水军判定的免责声明——**每条可疑判定都要带上它**，防止被当成结论使用。
WATER_DISCLAIMER = ('水军信号是**启发式提示**，不是结论：真实用户也可能命中其中一条。'
                    '正确用法是「把它们排在后面看」，不是「直接删掉」。')

#: 模板化雷同的判据阈值（只在同批 ≥3 条时才启用，避免两条撞车就误报）。
_DUP_RATIO = 0.80
_DUP_MIN_PEERS = 3
_DUP_MIN_LEN = 8


def _read_json(path: str) -> dict:
    raw = Path(path).read_text(encoding='utf-8')
    return json.loads(raw)


def _texts(evidence: dict) -> list:
    """把一处 place_evidence 的所有自由文本摊平，供死线正则扫描。"""
    out = []
    for key in ('name', 'features', 'why_visit', 'caveats', 'best_time',
                'physical_load', 'note'):
        value = evidence.get(key)
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, list):
            out.extend(str(item) for item in value)
    return out


def _level_hint(note: dict) -> dict:
    """给一条笔记建议证据等级与理由。**建议不是结论**，写路书时仍按证据规则判。"""
    connector = str(note.get('connector_type') or '').strip()
    platform = str(note.get('platform') or '') + str(note.get('author_display_name') or '')
    level = _LEVEL_BY_CONNECTOR.get(connector, 'C')
    reason = '索引到的摘要不是第二来源'
    if connector == 'web_search_fallback':
        reason = '搜索索引/转载，只能算单源线索'
    elif connector == 'user_provided':
        reason = '用户提供，未经工具实证'
    elif connector == 'browser':
        reason = '页面直读（本次未接通）'
    if any(word in platform for word in _OFFICIAL_HINTS):
        level = 'B'
        reason = '官方机构发布，配合媒体转载可算两个独立来源'
    return {'suggested_level': level, 'reason': reason}


#: 身份识别词——比 `_OFFICIAL_HINTS` 更严，因为身份会决定这条说法怎么用，
#: 判错比判不出更糟。认不出一律 UNKNOWN。
_OFFICIAL_VOICE_HINTS = ('文旅', '旅游局', '文化和旅游', '旅游协会', '人民政府', '政府',
                         '官方', '发布厅', '体育局', '园林局', '文化广电')


def _infer_voice(note: dict) -> str:
    """推断「谁在说」。显式写了就用显式的；没写按平台名推；认不出报 UNKNOWN。

    **保守优先**：赌错身份会让一条广告被当成官方口径，或反过来把官方发布
    当成软广。所以宁报 UNKNOWN，不硬塞。
    """
    explicit = str(note.get('voice') or '').strip().upper()
    if explicit in VOICE_KINDS:
        return explicit
    blob = '%s %s' % (note.get('platform') or '', note.get('author_display_name') or '')
    for hints, kind in ((_MEDIA_HINTS, 'MEDIA'),
                        (_OFFICIAL_VOICE_HINTS, 'OFFICIAL'),
                        (_LOCAL_HINTS, 'LOCAL'),
                        (_EDITOR_HINTS, 'EDITOR'),
                        (_CREATOR_HINTS, 'CREATOR')):
        if any(h in blob for h in hints):
            return kind
    return 'UNKNOWN'


def _promo_flags(text: str) -> list:
    """挑出推广/夸大词。**返回信号，不是判定**——真心推荐也会用这些词。"""
    return [w for w in _PROMO_WORDS if w in text]


def _review_water_signals(review: dict, peers: list) -> list:
    """给一条评价算「可疑信号」。**只返回信号，任何情况下都不返回结论。**

    三类判据，全部是启发式：
      ① 空洞——只有泛化夸赞，没有任何可核对的具体信息（无地点/数字/动作）
      ② 雷同——与同批另一条高度相似（整批复制粘贴的典型形态）
      ③ 账号——昵称呈批量特征（默认昵称、纯数字后缀）

    单独任何一条都可能误伤真实用户，所以命中只意味着「排后面看」。
    """
    reasons = []
    text = str(review.get('text') or '').strip()
    account = str(review.get('account_hint') or '').strip()

    if len(text) < 6:
        reasons.append('过短（不足 6 字），没有可核对的信息')
    else:
        has_detail = bool(_DETAIL_RE.search(text))
        if not has_detail and any(w in text for w in _GENERIC_PRAISE):
            reasons.append('只有泛化夸赞、没有任何具体细节（无地点 / 数字 / 动作）')
        hits = _promo_flags(text)
        if not has_detail and hits:
            reasons.append('命中推广词（%s）且无细节' % '、'.join(hits[:3]))

    if account and _WATER_ACCOUNT_RE.match(account):
        reasons.append('账号名呈批量特征（默认昵称 / 数字后缀）')

    if len(peers) >= _DUP_MIN_PEERS and len(text) >= _DUP_MIN_LEN:
        for other in peers:
            # ⚠️ 跳过自己必须按**对象身份**，不能按内容比较——
            # 三条一模一样的评论（整批复制的典型形态）按内容比会互相跳过，
            # 于是最该被抓的那一组恰好全部漏网。这个 bug 实测踩到过。
            if other is review:
                continue
            otext = str(other.get('text') or '').strip()
            if len(otext) < _DUP_MIN_LEN:
                continue
            ratio = difflib.SequenceMatcher(None, text, otext).ratio()
            if ratio >= _DUP_RATIO:
                reasons.append('与同批另一条评价高度雷同（相似度 %.0f%%）' % (ratio * 100))
                break
    return reasons


def _norm_place(name: str) -> str:
    """地点名归一化——**只去空白与全角空格**，不做模糊合并。

    为什么不合并「孙中山故居」与「孙中山故居纪念馆」：错合并会把两个地方的
    说法混在一起，制造出一个不存在的共识。**宁可漏合并，不可错合并**。
    """
    return re.sub(r'[\s\u3000]+', '', name or '').lower()


def triangulate(notes: list) -> dict:
    """按地点聚合多个来源的说法——回答「这是共识，还是只有一个人在说」。

    「综合多来源、多角度判断」在机器上能可靠做到的部分是**数独立来源**，
    不是判语义冲突。所以本函数只做三件事，不越界：

      ① 同一个地点被几个**独立域名**提到（同域转载不算两个来源）；
      ② 每个来源的**身份**（谁在说）分布；
      ③ 把各来源的原话**并列摆出来**——矛盾与否由人判，不由机器裁。
    """
    places: dict = {}
    for note in notes:
        if not isinstance(note, dict):
            continue
        url = str(note.get('url') or '').strip()
        domain = urlparse(url).netloc.lower() or 'unknown'
        voice = _infer_voice(note)
        for ev in (note.get('place_evidence') or []):
            if not isinstance(ev, dict):
                continue
            raw = str(ev.get('name') or '').strip()
            key = _norm_place(raw)
            if not key:
                continue
            rec = places.setdefault(key, {'place': raw, 'sources': []})
            say = ' / '.join(str(ev.get(f) or '') for f in ('features', 'why_visit')).strip(' /')
            rec['sources'].append({
                'domain': domain,
                'voice': voice,
                'title': note.get('title'),
                'url': url,
                'checked_at': note.get('checked_at'),
                'says': (say[:160] + '…') if len(say) > 160 else say,
            })

    rows = []
    for rec in places.values():
        domains = {s['domain'] for s in rec['sources']}
        voices = sorted({s['voice'] for s in rec['sources']})
        review_count = sum(len(n.get('reviews') or []) for n in notes
                           if isinstance(n, dict)
                           and any(_norm_place(str((e or {}).get('name') or '')) == _norm_place(rec['place'])
                                   for e in (n.get('place_evidence') or [])))
        rows.append({
            'place': rec['place'],
            'mentions': len(rec['sources']),
            'independent_sources': len(domains),
            'voices': voices,
            'reviews_seen': review_count,
            'status': 'CROSS_CHECKED' if len(domains) >= 2 else 'SINGLE_SOURCE',
            'sources': rec['sources'],
        })

    rows.sort(key=lambda r: (-r['independent_sources'], -r['mentions'], r['place']))
    cross = [r for r in rows if r['status'] == 'CROSS_CHECKED']
    return {
        'places': rows,
        'summary': {
            'places_total': len(rows),
            'cross_checked': len(cross),
            'single_source': len(rows) - len(cross),
            'voices': sorted({v for r in rows for v in r['voices']}),
            'note': ('独立来源 ≥2 才算交叉印证；**单源不等于错，只是还没人替你复核**。'
                     '本报告只做并列，不裁决冲突——冲突项要人工读原话，'
                     '按 evidence-rules.md 第 ④ 条「矛盾项并列」处理。'),
        },
    }


def _checked_date(note: dict):
    """取一条笔记的核实日期；取不到返回 None（无时戳 = 无法判断新鲜度）。"""
    raw = str(note.get('checked_at') or '').strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _is_stale(note: dict, stale_days: int) -> bool:
    """超过 stale_days 天（或压根没有时戳）→ 视为需要重核。"""
    checked = _checked_date(note)
    if checked is None:
        return True
    return (date.today() - checked).days > stale_days


def _note_key(note: dict):
    """去重键：优先 url（可回溯），没有就退化为标题。"""
    url = str(note.get('url') or '').strip()
    if url:
        return ('url', url)
    return ('title', str(note.get('title') or '').strip())


def merge(existing: dict, new_notes: list, stale_days: int = 90) -> tuple:
    """把本次采到的笔记并入已有线索卡——「一次采集、多趟复用」的机器实现。

    规则：
      · 本次重采到的（同 key）用新条目，旧的丢弃并计入 `duplicates_dropped`；
      · 旧卡里本次没采到的**保留**，标 `carried_over: true`（沿用）；
      · 沿用条目按时效判：超期或从无时戳 → 再加 `stale: true`，等下次重核。

    保留而不清空，是这一步存在的理由：清空就等于每趟从零开始。
    """
    old = [n for n in (existing.get('notes') or []) if isinstance(n, dict)]
    fresh_keys = {_note_key(n) for n in new_notes}
    merged = list(new_notes)
    carried = stale = duplicated = 0
    for note in old:
        if _note_key(note) in fresh_keys:
            duplicated += 1
            continue
        item = dict(note)
        item['carried_over'] = True
        if _is_stale(item, stale_days):
            item['stale'] = True
            stale += 1
        carried += 1
        merged.append(item)
    stats = {'new': len(new_notes), 'carried_over': carried,
             'stale': stale, 'duplicates_dropped': duplicated,
             'stale_days': stale_days}
    return merged, stats


def coverage(report: dict, merge_stats: dict = None) -> dict:
    """本次情报采集的覆盖事实——给「社会情报已声明」那道闸门用的数字。

    刻意不进任何评分：它只回答「这次到底采到了什么、够不够撑起声明」。
    """
    levels = {}
    for entry in report.get('evidence_hint', []):
        key = entry.get('suggested_level') or 'C'
        levels[key] = levels.get(key, 0) + 1
    connectors = sorted({str(n.get('connector_type') or '').strip()
                         for n in report.get('notes', []) if n.get('connector_type')})
    voices = dict(report.get('voices') or {})
    if not voices:                      # 喂进来的是旧卡（还没跑过 voice 推断）
        for note in report.get('notes', []):
            kind = str(note.get('voice') or 'UNKNOWN')
            voices[kind] = voices.get(kind, 0) + 1
    # UNKNOWN 排最后：它是「判不出身份」的兜底桶，不该顶在第一位显得很重要
    ordered = {k: voices[k] for k in sorted(voices) if k != 'UNKNOWN'}
    if 'UNKNOWN' in voices:
        ordered['UNKNOWN'] = voices['UNKNOWN']
    reviews = sum(len(n.get('reviews') or []) for n in report.get('notes', []))
    out = {
        'notes': len(report.get('notes', [])),
        'by_level': {k: levels[k] for k in sorted(levels)},
        'connector_types': connectors,
        'voices': ordered,
        'reviews': reviews,
        'errors': len(report.get('errors', [])),
        'warnings': len(report.get('warnings', [])),
    }
    if merge_stats:
        out.update(merge_stats)
    return out


def normalize(doc: dict) -> dict:
    """校验并归一化一份线索草稿。返回结构化报告，不抛异常。"""
    errors = []
    warnings = []
    hints = []
    notes_out = []

    notes = doc.get('notes')
    if not isinstance(notes, list) or not notes:
        return {
            'status': 'INVALID',
            'destination': doc.get('destination'),
            'notes': [], 'errors': ['没有任何笔记（notes 为空）'],
            'warnings': [], 'evidence_hint': [],
        }

    for index, note in enumerate(notes, 1):
        if not isinstance(note, dict):
            errors.append(f'第 {index} 条笔记不是对象')
            continue
        label = str(note.get('title') or note.get('url') or f'第 {index} 条')

        url = str(note.get('url') or '').strip()
        if not url:
            # compile-research 会把它降级成 warning，但草稿阶段就该拦住：
            # 没有 URL 的线索无法回溯，等于不可核验。
            errors.append(f'「{label}」缺 url——无法回溯的线索不算证据')
        checked_at = str(note.get('checked_at') or '').strip()
        if not checked_at:
            errors.append(f'「{label}」缺 checked_at（什么时候查的）')

        connector = str(note.get('connector_type') or '').strip()
        if connector not in _CONNECTOR_TYPES:
            errors.append(
                f'「{label}」的 connector_type 非法（{connector or "缺"}）——'
                f'必须是 {sorted(_CONNECTOR_TYPES)} 之一，来源要自报'
            )
        login_state = (note.get('source') or {}).get('login_state')
        if connector == 'browser' and not login_state:
            warnings.append(f'「{label}」走 browser 却没写 login_state')

        claims = note.get('claims') or []
        if not isinstance(claims, list):
            errors.append(f'「{label}」的 claims 不是数组')
            claims = []
        for claim_index, claim in enumerate(claims, 1):
            if not isinstance(claim, dict):
                errors.append(f'「{label}」第 {claim_index} 条 claim 不是对象')
                continue
            ctype = str(claim.get('type') or '').strip().upper().replace(' / ', '_')
            ctype = _CLAIM_ALIASES.get(ctype, ctype)
            text = str(claim.get('text') or '').strip()
            if ctype not in CLAIM_TYPES:
                errors.append(
                    f'「{label}」第 {claim_index} 条 claim 类别非法：{ctype or "缺"}'
                    f'——证据类别只有六种，自创一个就绕过了那张表'
                )
                continue
            if not text:
                errors.append(f'「{label}」第 {claim_index} 条 claim 没有 text')
                continue

            if _PRICE_RE.search(text):
                if ctype != 'PRICE_SIGNAL':
                    errors.append(
                        f'「{label}」把价格写进了 {ctype}：「{text[:40]}」'
                        f'——价格只能以 PRICE_SIGNAL 出现'
                    )
                elif not (claim.get('unverified') or claim.get('confidence')):
                    errors.append(
                        f'「{label}」的 PRICE_SIGNAL 没标未核实：「{text[:40]}」'
                        f'——必须标 unverified，且不得进 estimated_cost'
                    )
            if _HOURS_RE.search(text):
                errors.append(
                    f'「{label}」写了营业时间：「{text[:40]}」'
                    f'——营业时间没有可承载的类别，必须回场馆或高德核成 [A]'
                )
            if _RAIL_RE.search(text):
                errors.append(
                    f'「{label}」写了车次/余票/时刻：「{text[:40]}」'
                    f'——这三类是 [A] 专属，必须回 12306 或官方'
                )

        evidence_items = note.get('place_evidence') or []
        if not isinstance(evidence_items, list):
            errors.append(f'「{label}」的 place_evidence 不是数组')
            evidence_items = []
        for ev_index, evidence in enumerate(evidence_items, 1):
            if not isinstance(evidence, dict):
                errors.append(f'「{label}」第 {ev_index} 处 place_evidence 不是对象')
                continue
            name = str(evidence.get('name') or '').strip()
            if not name:
                errors.append(f'「{label}」第 {ev_index} 处 place_evidence 缺 name')
                continue
            blob = ' '.join(_texts(evidence))
            if _HOURS_RE.search(blob):
                errors.append(
                    f'「{label}」的「{name}」里写了营业时间——'
                    f'必须回场馆官网或高德 POI 核成 [A]'
                )
            if _RAIL_RE.search(blob):
                errors.append(f'「{label}」的「{name}」里写了车次/余票/时刻——[A] 专属')
            if _PRICE_RE.search(blob) and not evidence.get('price_signal'):
                errors.append(
                    f'「{label}」的「{name}」里出现了价格痕迹，'
                    f'要么删掉，要么显式标 price_signal: true 并注明未核实'
                )
            missing = [
                field for field in ('features', 'why_visit', 'suggested_duration_minutes')
                if not evidence.get(field)
            ]
            if missing:
                warnings.append(
                    f'「{label}」的「{name}」缺 {"/".join(missing)}——'
                    f'景点卡不完整，补齐后才能进路线'
                )

        if not claims and not evidence_items:
            warnings.append(f'「{label}」既没有 claims 也没有 place_evidence，等于没读到东西')

        # ---- 口碑层：谁在说 / 评价与评论 / 夸大信号
        declared_voice = str(note.get('voice') or '').strip().upper()
        voice = _infer_voice(note)
        note['voice'] = voice
        if voice == 'UNKNOWN' and declared_voice not in VOICE_KINDS:
            warnings.append(
                f'「{label}」的说话人身份判不出来——口碑层按最保守处理。'
                f'能确认就显式写 voice（{" / ".join(sorted(VOICE_KINDS))}）')

        reviews = note.get('reviews') or []
        if not isinstance(reviews, list):
            errors.append(f'「{label}」的 reviews 不是数组')
            reviews = []
        clean_reviews = []
        for ri, review in enumerate(reviews, 1):
            if not isinstance(review, dict):
                errors.append(f'「{label}」第 {ri} 条 review 不是对象')
                continue
            rtext = str(review.get('text') or '').strip()
            if not rtext:
                errors.append(f'「{label}」第 {ri} 条 review 没有 text')
                continue
            stance = str(review.get('stance') or '').strip().upper()
            if stance not in STANCES:
                errors.append(f'「{label}」第 {ri} 条 review 的 stance 非法（{stance or "缺"}）'
                              f'——只能是 {" / ".join(STANCES)}')
                continue
            rsrc = str(review.get('source') or 'COMMENT').strip().upper()
            if rsrc not in ('NOTE', 'COMMENT'):
                errors.append(f'「{label}」第 {ri} 条 review 的 source 只能是 NOTE 或 COMMENT')
                continue
            review['source'] = rsrc
            review['stance'] = stance
            # 死线一视同仁：评论区里说的价格/时间/车次一样不许进路书
            if _HOURS_RE.search(rtext):
                errors.append(f'「{label}」第 {ri} 条 review 写了营业时间：「{rtext[:30]}」'
                              f'——评论区和正文一个标准')
            if _RAIL_RE.search(rtext):
                errors.append(f'「{label}」第 {ri} 条 review 写了车次/余票/时刻：「{rtext[:30]}」')
            if _PRICE_AMOUNT_RE.search(rtext) and not review.get('price_signal'):
                errors.append(f'「{label}」第 {ri} 条 review 出现具体金额：「{rtext[:30]}」'
                              f'——评价里的金额只是信号，须显式标 price_signal: true 并注明未核实')
            clean_reviews.append(review)

        # 水军信号在**整批**上算（雷同判据要横向比），所以放在收齐之后
        for review in clean_reviews:
            signals = _review_water_signals(review, clean_reviews)
            if signals:
                review['water_suspect'] = True
                review['water_reasons'] = signals
                review['water_disclaimer'] = WATER_DISCLAIMER
            else:
                review.setdefault('water_suspect', False)
        if clean_reviews:
            note['reviews'] = clean_reviews

        # 夸大信号：扫正文与所有 claim（评价文本另算在 water_signals 里，不重复挂）
        promo = set(_promo_flags(str(note.get('visible_body') or '')))
        for claim in claims:
            if isinstance(claim, dict):
                promo |= set(_promo_flags(str(claim.get('text') or '')))
        if promo:
            note['promo_flags'] = sorted(promo)

        hint = _level_hint(note)
        entry = {'title': label, 'platform': note.get('platform'), **hint}
        if note.get('carried_over'):
            entry['carried_over'] = True      # 沿用自旧卡：不是本次采到的新情报
        if note.get('stale'):
            entry['stale'] = True             # 且已超期，开工前须重核
        hints.append(entry)
        notes_out.append(note)

    # ---- 口碑层汇总：这批情报里，机构口径与真人评价各占多少
    voices: dict = {}
    for note in notes_out:
        kind = str(note.get('voice') or 'UNKNOWN')
        voices[kind] = voices.get(kind, 0) + 1
    review_total = sum(len(n.get('reviews') or []) for n in notes_out)
    review_suspect = sum(1 for n in notes_out for r in (n.get('reviews') or [])
                         if isinstance(r, dict) and r.get('water_suspect'))
    promo_notes = sum(1 for n in notes_out if n.get('promo_flags'))

    status = 'INVALID' if errors else ('VALID_WITH_WARNINGS' if warnings else 'VALID')
    return {
        'status': status,
        'destination': doc.get('destination'),
        'travel_style': doc.get('travel_style'),
        'notes': notes_out,
        'errors': errors,
        'warnings': warnings,
        'evidence_hint': hints,
        'voices': {k: voices[k] for k in sorted(voices)},
        'credibility': {
            'reviews': review_total,
            'water_suspects': review_suspect,
            'notes_with_promo_flags': promo_notes,
            'note': ('水军信号是启发式提示，**不是结论**；promo_flags 同理——'
                     '命中推广词不等于内容为假。两者都只用于「排后面看」。'),
        },
    }


def print_credibility(report: dict) -> None:
    """打印「谁在说」的分布——口碑层的第一眼结论。"""
    voices = report.get('voices') or {}
    cred = report.get('credibility') or {}
    if not voices:
        return
    parts = ['%s %d' % (VOICE_KINDS.get(k, k).split('（')[0], v)
             for k, v in voices.items()]
    print('\n口碑构成：' + ' ｜ '.join(parts))
    if voices.get('CREATOR'):
        print('  ⚠️ 有 %d 条来自内容创作者——**夸大风险最高的一类**，'
              '其说法进路书前至少要有第二个来源罩住。' % voices['CREATOR'])
    # 注意：评论区是**挂在 note 上的**（reviews），不会把 note 的 voice 变成 COMMENT。
    # 所以这里要同时看 voices 与 reviews 两个来源，否则会误报「一条评价都没有」。
    if not any(k in voices for k in ('TRAVELER', 'LOCAL')) and not cred.get('reviews'):
        print('  ⚠️ **这批情报里没有任何真人评价或评论**——全是机构/编辑/创作者口径。'
              '「用户怎么说」这一层还是空的，路书里不得写成「游客普遍反映」。')
    if cred.get('reviews'):
        print('  评价/评论 %d 条，其中水军信号 %d 条（**信号不是结论**，只用于排后面看）'
              % (cred['reviews'], cred.get('water_suspects', 0)))
    if cred.get('notes_with_promo_flags'):
        print('  %d 条命中推广/夸大词——同样只是信号，真心推荐也会用这些词'
              % cred['notes_with_promo_flags'])


def print_triangulation(tri: dict) -> None:
    """打印三角验证结果：共识 / 单源 / 谁在说 / 原话并列。"""
    s = tri['summary']
    print('\n' + '=' * 88)
    print('多来源交叉印证　地点 %d 个：交叉印证 %d ｜ 单源 %d'
          % (s['places_total'], s['cross_checked'], s['single_source']))
    print('=' * 88)
    if not tri['places']:
        print('  没有可聚合的地点（place_evidence 为空）——'
              '先让线索卡带上 place_evidence，这一步才有东西可算。')
        return
    for row in tri['places']:
        mark = '✅' if row['status'] == 'CROSS_CHECKED' else '◻'
        voices = '/'.join(row['voices'])
        print('\n%s %s　独立来源 %d ｜ 提及 %d ｜ 谁在说：%s'
              % (mark, row['place'], row['independent_sources'], row['mentions'], voices))
        for src in row['sources']:
            print('    [%s] %s' % (src['voice'], src['domain']))
            if src.get('says'):
                print('        %s' % src['says'])
    print('\n%s' % s['note'])


def main() -> int:
    parser = argparse.ArgumentParser(
        description='社媒 / 网页线索归一化 + 死线校验（营业时间/票价/车次不进社区笔记）'
    )
    parser.add_argument('--input', required=True, help='线索草稿 JSON')
    parser.add_argument('--triangulate', action='store_true',
                        help='按地点聚合多来源说法：独立来源数 / 谁在说 / 原话并列')
    parser.add_argument('--brief', action='store_true', help='顺带汇编成景点卡（调 compile-research）')
    parser.add_argument('--json', action='store_true', help='输出结构化 JSON')
    parser.add_argument('-o', '--output', help='把归一化结果写到文件')
    parser.add_argument('--merge', metavar='已有线索卡.json',
                        help='增量并入已有线索卡：按 url 去重，旧条目标 carried_over（超期再加 stale）')
    parser.add_argument('--stale-days', type=int, default=90,
                        help='并入时判定「超期需重核」的天数阈值（默认 90）')
    args = parser.parse_args()

    if _IMPORT_ERROR is not None:
        print(f'[X] 引擎导入失败：{_IMPORT_ERROR}', file=sys.stderr)
        return 1
    try:
        doc = _read_json(args.input)
    except (OSError, ValueError) as exc:
        print(f'[X] 读不了 {args.input}：{exc}', file=sys.stderr)
        return 1

    report = normalize(doc)

    merge_stats = None
    merge_source = None
    if args.merge:
        try:
            existing = _read_json(args.merge)
        except (OSError, ValueError) as exc:
            print(f'[X] 读不了合并源 {args.merge}：{exc}', file=sys.stderr)
            return 1
        merge_source = args.merge
        merged_notes, merge_stats = merge(existing, report['notes'], args.stale_days)
        # 合并后的整卡再走一遍归一化：沿用回来的旧条目同样受死线约束，
        # 「上次写对了所以这次不查」是这个 skill 最不能接受的心态。
        report = normalize({'destination': doc.get('destination'),
                            'travel_style': doc.get('travel_style'),
                            'notes': merged_notes})
        report['merge'] = merge_stats
        report['merge_source'] = merge_source

    report['coverage'] = coverage(report, merge_stats)
    if args.triangulate:
        report['triangulation'] = triangulate(report['notes'])

    if args.brief and report['notes']:
        brief = compile_destination_brief({
            'destination': doc.get('destination'),
            'travel_style': doc.get('travel_style'),
            'notes': report['notes'],
        })
        report['destination_brief'] = brief

    if args.output:
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
        )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print('=' * 88)
        print(f'社媒线索归一化　{doc.get("destination") or "未命名目的地"}')
        print('=' * 88)
        for entry in report['evidence_hint']:
            tag = ' ⇢沿用' if entry.get('carried_over') else ''
            print(f'  [{entry["suggested_level"]}] {entry["title"][:44]:<44} {entry["reason"]}{tag}')
        cov = report['coverage']
        lv = '·'.join(f'{k}{v}' for k, v in cov['by_level'].items()) or '无'
        print(f'\n覆盖：{cov["notes"]} 条（{lv}）｜ 路径 '
              f'{"、".join(cov["connector_types"]) or "—"}')
        if merge_source:
            print(f'  并入 {merge_source}：新增 {cov["new"]} ｜ 沿用 {cov["carried_over"]}'
                  f'（其中超期 {cov["stale"]}，阈值 {cov["stale_days"]} 天）'
                  f' ｜ 本次重采覆盖旧条目 {cov["duplicates_dropped"]}')
            if cov['stale']:
                print('  ⚠️ 超期条目下次开工会先重核——沿用不等于新鲜')
        if report['errors']:
            print(f'\n[X] {len(report["errors"])} 处必须改（改完再进管线）：')
            for item in report['errors']:
                print(f'    · {item}')
        if report['warnings']:
            print(f'\n[!] {len(report["warnings"])} 处提醒：')
            for item in report['warnings']:
                print(f'    · {item}')
        if 'destination_brief' in report:
            brief = report['destination_brief']
            cards = brief['attraction_cards']
            print(f'\n景点卡 {len(cards)} 张（{brief["status"]}）：')
            for card in cards:
                flag = '缺 ' + '/'.join(card['missing_fields']) if card['missing_fields'] else '齐'
                print(f'    · {card["name"]:<14} 证据 {card["evidence_count"]} 条　{flag}')
        print_credibility(report)
        if 'triangulation' in report:
            print_triangulation(report['triangulation'])
        if not report['errors'] and not report['warnings']:
            print('\n✅ 全部通过')

    return 2 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
