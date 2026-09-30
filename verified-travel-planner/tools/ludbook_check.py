#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ludbook_check.py — 路书交付自查（30 项机械核对）

为什么需要它：AI 做攻略最容易翻车的地方不是"不会写"，是"看起来写完了"。
车程是估的、票价是旧的、改了一处忘了另一处、配图张数凑不够——这些问题
通读一遍都发现不了，必须靠机器数。

本脚本把"看起来差不多"变成"机器数得出来的相等"，在交付前硬拦一道。

用法：
    python ludbook_check.py 路书_XX_v1.md [更多.md ...] [--days 3] [--json]
    python ludbook_check.py 路书_XX_v1.html --html [--days 3]

退出码：0 = 可交付；1 = 有阻断项未过；2 = 文件读不到

────────────────────────────────────────────────────────────────────────
覆盖口径（**重要，别把「全绿」当「30 项全过」**）

30 项里本脚本能机械判定的只有 23 项（19 阻断 + 4 提示），另有：
  · 条件项 2 项 —— ㉒ 停车信息（仅自驾）、㉓ 景区示意图（仅要求出图时）
  · 人工项 5 项 —— ⑰ 信源店用透（跨文件）、⑲ 配图逐张亲验、⑳ 渲染截图亲验、
                    ㉔ 同一决策点全文同步、㉕ 修正双写（后两项需事实源↔HTML 对照）

每轮运行结尾都会打印**本次实际覆盖清单**，未覆盖项逐条给出确认方式。
「全绿」只代表脚本能查的那部分没问题——**它不等于 30 项全过**。

新增的 E 组（㉗）是**社会情报覆盖**：情报采集是本 skill 的默认动作，
但「跳过一个默认动作」是允许的——**前提是留痕**。采了就写清了来源与路径，
没采就写清理由；静默跳过才拦。这条规则的口径是「报『没有』比报假的『有』值钱」。

md 与 html 走同一套 A/B/D 组检查（HTML 先去标签再查），
HTML 模式另外叠加 C 组产物检查（配图真数/来源标注/动效两态/交互/视口）。
"""
import io
import re
import sys
import json
import argparse
import os

# ---- 终端符号自适应：中文 Windows 黑框(GBK)认不出 ✅❌ 时自动退 ASCII
_SYM_FALLBACK = str.maketrans({
    '✅': '[OK]', '✓': '[OK]', '√': '[OK]',
    '❌': '[X]', '✗': '[X]', '×': '[X]',
    '→': '->', '■': '[*]', '●': '[*]', '·': '.',
    '⚠': '(!)', '🔴': '(*)', '｜': '|', '─': '-', '—': '-',
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


def read(p):
    if not os.path.exists(p):
        emit("FATAL 文件不存在: %s" % p)
        sys.exit(2)
    return io.open(p, encoding="utf-8", errors="replace").read()


# ============================================================ 30 项元数据
# level: BLOCK=机械阻断 / WARN=机械提示 / COND=条件项(本次未必适用) / MANUAL=需人工
# 元数据是**覆盖声明的事实来源**：main() 结尾用它算出「本次实检了几项」，
# 所以新增/改动检查项时，这里必须同步——否则覆盖声明会说谎。
ITEMS = [
    ('①', 'A', '数字溯源报数',                'BLOCK',  ''),
    ('②', 'A', '证据分级标注',                'BLOCK',  ''),
    ('③', 'A', 'A 级信息时效',                'BLOCK',  ''),
    ('④', 'A', '矛盾项并列',                  'BLOCK',  ''),
    ('⑤', 'A', '能力降级已声明',              'WARN',   '需判断「阶段 1 测出的不可用项是否写进路书」'),
    ('⑥', 'A', '未核实项清单',                'BLOCK',  ''),
    ('⑦', 'B', '全文一致（无已取消项残留）',   'WARN',   ''),
    ('⑧', 'B', '天气已分时段',                'BLOCK',  ''),
    ('⑨', 'B', '门票放票时间+预约截止',        'BLOCK',  ''),
    ('⑩', 'B', '店铺有地址+营业状态已核',      'BLOCK',  '粗查：地址式样 + 营业状态词'),
    ('⑪', 'B', '动线无回头路',                'BLOCK',  ''),
    ('⑫', 'B', '预算闭合+隐藏成本有行',        'BLOCK',  ''),
    ('⑬', 'B', '避雷=真实场景+应对',           'BLOCK',  ''),
    ('⑭', 'B', '应急 Plan B',                'BLOCK',  ''),
    ('⑮', 'B', '六维度板块齐全',              'BLOCK',  ''),
    ('⑯', 'B', '餐行机械钩报数',              'BLOCK',  ''),
    ('⑰', 'B', '信源店用透',                  'MANUAL', '需与美食情报卡跨文件对照'),
    ('⑱', 'C', '每日配图齐全+来源标注',        'BLOCK',  ''),
    ('⑲', 'C', '配图逐张亲验',                'MANUAL', '图注落位对 ≠ 图内容对，须逐张目视'),
    ('⑳', 'C', '页面渲染截图亲验',            'MANUAL', '真机桌面+手机各至少一张'),
    ('㉑', 'C', '入场动效两态',                'BLOCK',  ''),
    ('㉒', 'C', '停车信息已查',                'COND',   '仅自驾行程适用'),
    ('㉓', 'C', '景区示意图',                  'COND',   '仅要求出图时适用'),
    ('㉔', 'D', '同一决策点全文同步',          'MANUAL', '需事实源↔HTML 对照，判据=旧值在活条款里归零'),
    ('㉕', 'D', '修正双写',                    'MANUAL', '需事实源↔HTML 关键决策点抽查'),
    ('㉖', 'D', '文案去 AI 味',                'BLOCK',  ''),
    ('㉗', 'E', '社会情报已声明',              'BLOCK',  '默认动作；未采集须写明理由，静默跳过拦下'),
    ('㉘', 'B', '主路线 + 备选路线齐备',        'BLOCK',  '用户要能对比方案：主线 1 条 + 备选 ≥2 条'),
    ('㉙', 'B', '沿途顺道点已声明',             'WARN',   '主线上确实没有可顺道的点时要写明原因'),
    ('㉚', 'B', '个性化偏好已声明',             'WARN',   '采集了用户自报偏好就当呈现；没采集也不拦（软字段）'),
]
# 注：项号按**追加顺序**编号，不按分组重排——重排会牵动全仓的编号交叉引用，
# 而分组字母（A-E）已经表达归属。E 组是本项新增的「情报覆盖」组。
# ㉘㉙ 归 B 组（内容完整）：路线结构属于「读者能不能对比方案」，不是证据问题。

# ============================================================ 检查项定义

# 高频 AI 味词（去 AI 味自查用）
AI_WORDS = ['进行', '值得注意', '本质上', '仿佛', '映入', '众所周知', '众所',
            '首先其次', '综上', '绝绝子', '震撼', '为您', '不仅.*而且',
            '此外', '标志着', '总而言之', '不言而喻', '值得一提']

# 词表是裸正则匹配、不做中文分词，所以合法的跨词切分会被误判。
# 实测：中山路书里「本次没排进行程」=「排进」+「行程」，被算成 1 次 AI 味词「进行」。
# 这类例外在统计前先从文本里剔除——否则只能去改本来没问题的文案来骗过闸门，
# 那是把闸门调松、不是把稿子改好。
AI_WORD_EXCEPTIONS = [
    '排进行程',   # 排进 + 行程
]

# 裸数字模式：需溯源的客观数据（时长/距离）
# 注意：景点「停留时长」是规划建议不是数据，豁免；只查带交通语义的时长
BARE_DIGIT_PATTERNS = [
    # 车程/通勤时长（客观数据，必须溯源）
    (re.compile(r'(?<![（(])\b\d+(?:\.\d+)?\s*(?:小时车程|分钟车程|小时路程)'), '车程'),
    # 距离
    (re.compile(r'(?<![（(])\b\d+(?:\.\d+)?\s*(?:公里|千米|km)\b(?![）)])'), '距离'),
]

# 交通语义：该行含这些词时，"X分钟/X小时"才算需溯源的车程数据
TRANSPORT_HINT = ['车程', '驾车', '开车', '驾车约', '路程', '车行', '通勤', '单程', '打车', '骑行', '步行约']

# 票价语义：铁律 1 的「票价」指门票/车票一类**决策依赖**的价格。
# 小吃单价、茶位费、伴手礼、打车估价属消费参考，不在其列——
# 否则伴手礼「¥40/袋」也会被要求溯源，闸门会因噪声失去威信。
PRICE_SEMANTIC = ['门票', '票价', '车票', '机票', '套票', '联票', '购票', '船票', '索道',
                  '房价', '每晚', '早餐价']

# 预算汇总/假设行：由下方逐项加总而来，不逐处要求溯源
BUDGET_HINT = ['预算', '合计', '总计', '假设', '人均', '按人头']

# 明确在声明"这条不可靠"的行：本身就是未核实的如实交代，不反过来要求它溯源
DISCLAIM_HINT = ['未核实', '未复现', '未取到', '单一来源', '单源', '请自行确认',
                 '自己再确认', '到门口先问', '不是门票', '非门票', '需现场确认',
                 '仅供参考', '以现场为准']

# 证据分级标记
EVIDENCE_MARKS = ['[A]', '[B]', '[C]', '[D]', '【A】', '【B】', '【C】', '【D】']

# 时戳模式： 2026-09-27 / 2026-09-27 13:00 查
STAMP_RE = re.compile(r'20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}')
QUERY_STAMP_RE = re.compile(r'20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}[^）)]{0,12}查')

# 来源词（用于溯源检查）
SOURCE_WORDS = ['官方公众号', '官网', '官方App', '官方APP', '小程序', '点评', '大众点评',
                '地图', '高德', '百度地图', '美团', '携程', '飞猪', '12306', '小红书',
                '抖音', 'B站', '去哪儿']

# 「未核实」类出口词（⑤ 与 ⑥ 共用同一组判据，避免两处口径漂移）
UNVERIFIED_RE = re.compile(r'\[D\]|【D】|未核实|未查到|未取到|无法确认|未能确认')
# 未核实清单的标题式样（⑥ 要求把散落的未核实项归集成清单）
UNVERIFIED_LIST_HEAD = ['未核实项清单', '未核实清单', '待核实清单', '待确认清单',
                        '需确认清单', '需你确认', '待你确认', '未核实项']

# ---- E 组（㉗ 社会情报覆盖）判据 ----
# 情报采集是默认动作，所以交付物里必须能读到它的痕迹。三种出口：
#   ① 采到了 → 写「情报来源」（渲染器按 meta.social_intel 自动注入这条）+ 走了哪条路径
#   ② 没采   → 明写「本次未采集社会情报」+ 理由
#   ③ 什么都没有 → 拦下（静默跳过默认动作，等于把这条能力又变回可选项）
INTEL_DECLARED = ('情报来源', '社会情报', '玩法情报', '社媒情报', '情报卡')
INTEL_SKIPPED = ('本次未采集社会情报', '本次未采集情报', '未做社会情报采集',
                 '本次不采集社会情报', '社会情报采集已跳过')
# 三条可行路径（见 references/social-intake.md）——声明必须带上走了哪条，便于回溯
INTEL_PATHS = ('官方文旅发布', '官方发布', '搜索引擎索引', '搜索索引', '用户投喂')

# 日程分块：md 形如 "## Day 1 / ## D1"，HTML 去标签后形如 "D1 · 标题"
_DAY_SPLIT_RE = [
    re.compile(r'(?:^|\n)[ \t]*(?:#{1,6}[ \t]*)?(?:Day|D)[ \t]*(\d+)[ \t]*[·、.．:：]'),
    re.compile(r'(?:^|\n)[ \t]*(?:#{1,6}[ \t]*)?第[ \t]*(\d+)[ \t]*[天日]'),
]


# ============================================================ HTML → 纯文本

# 块级结束标签换成换行，行内标签（<b>/<span>/<a>）直接删——
# 否则 "<b>打车 35 分钟</b>（高德，2026-09-27 查）" 会被拆成两行，
# ① 的数字溯源检查就会把带来源的那行判成"裸数字"（误报）。
_BLOCK_TAGS = re.compile(
    r'</(?:p|div|li|ul|ol|h[1-6]|tr|td|th|figure|figcaption|section|article'
    r'|header|footer|table|blockquote|dl|dt|dd)\s*>|<br\s*/?\s*>', re.I)


def html_to_text(html):
    """把渲染产物还原成可供关键词/逐行检查的纯文本。

    顺序要紧：先处理表格（整表溯源判定），再剥 base64（否则 700KB 载荷会让
    逐行正则疯跑且误报），然后剥 style/script/注释，最后才处理标签。
    返回 (纯文本, meta)
    """
    html, st = _shield_tables(html)
    t = re.sub(r'data:image/[A-Za-z0-9+/=;,\-]+', ' ', html)
    t = re.sub(r'<(style|script)\b[^>]*>.*?</\1\s*>', ' ', t, flags=re.I | re.S)
    t = re.sub(r'<!--.*?-->', ' ', t, flags=re.S)
    t = _BLOCK_TAGS.sub('\n', t)
    t = re.sub(r'<[^>]*>', '', t)
    for a, b in (('&nbsp;', ' '), ('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                 ('&quot;', '"'), ('&#39;', "'"), ('&mdash;', '—'), ('&middot;', '·')):
        t = t.replace(a, b)
    t = re.sub(r'[ \t\u3000]+', ' ', t)
    t = re.sub(r'\n[ \t]+', '\n', t)
    t = re.sub(r'\n{2,}', '\n', t)
    return t, {'无来源表格': st['unfound'], '表格数值': st['nums']}


def _split_days(text):
    """按 Day/D1/第X天 标题切块，返回 [块文本, ...]（不含标题前的引言）

    注意：同一编号常出现两次——「速览/导航区」一次、「正文区」一次
    （如中山那份被切成 6 块：第 1~3 天 + D1~D3）。不去重会让餐行检查
    拿导航块去查早/午/晚，必然误报。故**同编号保留文本最长的那块**（正文）。
    """
    hits = []
    for rx in _DAY_SPLIT_RE:
        for m in rx.finditer(text):
            hits.append((m.start(), m.group(1)))
    if not hits:
        return []
    hits.sort()
    raw = []
    for i, (s, no) in enumerate(hits):
        e = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        raw.append((no, text[s:e]))

    def _nokey(x):
        try:
            return int(x)
        except ValueError:
            return 10 ** 6

    best = {}
    for no, blk in raw:
        # 同编号取最长块；等长时保留先出现的
        if no not in best or len(blk) > len(best[no]):
            best[no] = blk
    return [best[k] for k in sorted(best, key=_nokey)]


# 表格整体：里面的数字通常共享表外的一处来源声明
_TABLE_RE = re.compile(r'<table\b[^>]*>.*?</table>', re.I | re.S)


def _shield_tables(html):
    """表格里的里程/时长往往共享表外一处来源声明（如「本表数据：高德路线，2026-09-27 查」）。

    逐行查会把 14 行里程报成 14 处「裸数字」——中山那份实测就报出 55 处噪声。
    故整表处理：
      · 表内或紧邻上下文能回溯到来源/时戳 → 撤出逐行检查，**并把表内数值登记进
        已溯源池**（这样正文里复述「11.7 公里」不再被当成新的裸数字）；
      · 回溯不到                        → 折叠成一行提示，让 ① 只报一次。
    返回 (变换后的 html, {'unfound': 无来源表格数, 'nums': 表内已溯源数值})
    """
    st = {'unfound': 0, 'nums': set()}

    def repl(m):
        blk = m.group(0)
        ctx = html[max(0, m.start() - 500): m.end() + 500]
        if any(w in ctx for w in SOURCE_WORDS) or STAMP_RE.search(ctx):
            st['nums'].update(re.findall(r'\d+(?:\.\d+)?', blk))
            return '\n'
        st['unfound'] += 1
        return '\n【表格·整表无来源声明】\n'

    return _TABLE_RE.sub(repl, html), st


# ============================================================ 共用内容检查（A/B/D 组）
def check_content(text, days, extra_sourced=None, raw_html=None):
    """A 组(证据溯源) + B 组(内容完整) + ㉖ 去 AI 味。

    md 与 html 共用：**这是本次改动的要点**——原先这些检查只在 md 路径上跑，
    HTML 交付路径实际只查了 C 组几项，导致「26 项全绿」名不副实。

    extra_sourced：调用方已知"已溯源"的数值（如 HTML 里整张已溯源表格内的数字）。
    返回 (阻断项, 提示项, 统计)
    """
    bad = []
    warn_items = []
    stat = {}
    lines = text.split('\n')

    # ---- A 组：证据与溯源 ----
    # ① 数字溯源
    # 需溯源对象：a) 交通时长/距离（含交通语义的行）  b) 票价（限票价语义行）
    # 豁免：a) 值级去重——同一数值若在别处已与来源/时戳同现，复述不再重复报
    #       b) 预算汇总/假设行、明确声明"未核实"的行、非票价语义的消费参考
    sourced_nums = set(extra_sourced or ())
    for line in lines:
        if any(w in line for w in SOURCE_WORDS) or STAMP_RE.search(line):
            sourced_nums.update(re.findall(r'\d+(?:\.\d+)?', line))

    bare_total = 0
    bare_samples = []
    for line in lines:
        if any(k in line for k in BUDGET_HINT) or any(k in line for k in DISCLAIM_HINT):
            continue
        if any(w in line for w in SOURCE_WORDS) or STAMP_RE.search(line):
            continue
        if '【估算】' in line or '[D]' in line or '【未核实】' in line:
            continue

        # --- 交通数据 ---
        has_transport = any(k in line for k in TRANSPORT_HINT)
        for rx, kind in BARE_DIGIT_PATTERNS:
            for m in rx.finditer(line):
                if kind == '车程' and not has_transport:
                    continue
                val = re.search(r'\d+(?:\.\d+)?', m.group())
                if val and val.group() in sourced_nums:
                    continue
                bare_total += 1
                if len(bare_samples) < 3:
                    bare_samples.append('%s(%s)' % (m.group().strip(), kind))

        # --- 票价金额：只在票价语义行里要求溯源 ---
        if any(k in line for k in PRICE_SEMANTIC):
            for m in re.finditer(r'(?:¥|￥)\s*\d+(?:\.\d+)?', line):
                val = re.search(r'\d+(?:\.\d+)?', m.group())
                if val and val.group() in sourced_nums:
                    continue
                bare_total += 1
                if len(bare_samples) < 3:
                    bare_samples.append('%s(票价)' % m.group().strip())

    stat['裸数字'] = bare_total
    if bare_total > 0:
        bad.append("① 数字无溯源 %d 处（如 %s）——车程/距离/票价需带来源或时戳，估算段须标【估算】或[D]"
                   % (bare_total, '、'.join(bare_samples)))

    # ② 证据分级标注
    # 判据：**全篇零等级标即阻断**，不再有「工具链没落地路径」的豁免
    # （2026-09-27 已补齐：事实源模板示范写法 / render_html.py 转徽章 / 基准骨架有 .ev 样式）。
    # 两种输入两种形态——事实源与 md 里是字面量 [A]，HTML 产物里已被渲染成 .ev 徽章，
    # 所以两边都数，避免「渲染完就查不到」。
    badge_count = len(re.findall(r'class="ev ev-[abcd]"', raw_html or ''))
    mark_count = sum(text.count(m) for m in EVIDENCE_MARKS)
    ev_count = badge_count + mark_count
    stat['证据标记数'] = ev_count
    stat['证据徽章数'] = badge_count
    has_src = any(w in text for w in SOURCE_WORDS)
    has_qstamp = bool(QUERY_STAMP_RE.search(text))
    stat['来源+时戳'] = '有' if (has_src and has_qstamp) else '无'
    if ev_count == 0:
        if has_src and has_qstamp:
            bad.append("② 全篇零证据等级标——有来源和时戳，但没挂 [A]/[B]/[C·单源]/[D]。"
                       "渲染后应是 .ev 徽章；工具链已补齐（模板｜渲染器｜骨架），这不是缺口")
        else:
            bad.append("② 既无等级标也无「来源+查询时戳」——推荐项必须说清"
                       "「从哪来、什么时候查的、够不够 A 级」（见 evidence-rules.md）")

    # ③ A级信息时效
    stamps = STAMP_RE.findall(text)
    stat['日期数'] = len(stamps)
    query_stamps = QUERY_STAMP_RE.findall(text)
    stat['带查时戳'] = len(query_stamps)
    if len(stamps) > 0 and len(query_stamps) == 0:
        bad.append("③ 有日期但无查询时戳——动态信息（票价/车次/航班）须写「2026-09-27 13:00 查·来源」")

    # ④ 矛盾项并列（检查有无"并列写出"的痕迹）
    if len(stamps) > 20 and not any(k in text for k in ['矛盾', '不一致', '并列', '两说']):
        bad.append("④ 信息量大但无冲突处理痕迹——多源不一致时应并列写出，不擅自裁决")

    # ⑤ 能力降级 / 未核实声明
    # 判据：既然路书里必然有查不到的东西，那就必须有 [D] 或「未核实」的出口。
    # 完全没有 = 要么真的全查到了（罕见），要么就是把没查到的当查到了（危险）。
    # 故此项为提示级：不计入阻断，但必须让使用者看见。
    unv_hits = UNVERIFIED_RE.findall(text)
    stat['未核实出口'] = len(unv_hits)
    if not unv_hits:
        warn_items.append("⑤ 全文无未核实项标注——确认是真全查到了，还是把没查到的当查到了（若确实全查到可忽略）")

    # ⑥ 未核实项清单（散落的 [D] 必须归集成清单，并给出用户的确认方式）
    if unv_hits and not any(k in text for k in UNVERIFIED_LIST_HEAD):
        bad.append("⑥ 有 %d 处未核实/未查到标注，但没有汇总清单——[D] 项须归集成清单，"
                   "逐条给出用户该怎么确认（打哪个电话/看哪个公众号/去哪比价）" % len(unv_hits))

    # ---- B 组：内容完整 ----
    # ⑦ 全文一致性（判据只能做到"检出取消动作并提示"——旧值是否真的归零，
    #    需要事实源↔HTML 对照，属 ㉔ 人工项。原来的实现只统计不判定，等于没查。）
    canceled = re.findall(r'已取消[^\n。]{0,40}', text)
    stat['已取消项'] = len(canceled)
    if canceled:
        warn_items.append("⑦ 检出「已取消」字样 %d 处（%s）——确认对应内容已从全文清除"
                          "（速览/清单/时段卡/预算表都要扫），不能只在正文里改一处"
                          % (len(canceled), '；'.join(x[:24] for x in canceled[:3])))

    # ⑧ 天气分时段
    has_weather = any(k in text for k in ['天气', '气温', '预报', '降水', '雷阵雨'])
    has_time_slot = any(k in text for k in ['上午', '下午', '午后', '傍晚', '夜间', '逐时段'])
    stat['天气'] = '有' if has_weather else '无'
    if has_weather and not has_time_slot:
        bad.append("⑧ 天气未分时段——午后雷阵雨≠全天不能玩，需按逐时段决策")

    # ⑨ 门票预约
    if '约' not in text and '预约' not in text:
        bad.append("⑨ 无预约信息——需给预约状态表（什么要约/放票时间/截止日/约满兜底）")

    # ⑩ 店铺「有地址 + 营业状态已核」（粗查：地址式样 vs 营业状态词）
    addr_hits = set(re.findall(
        r'[\u4e00-\u9fa5A-Za-z0-9]{2,18}(?:路|街|大道|大街|巷|里)\s*\d+\s*号?', text))
    has_status = any(k in text for k in ['营业', '歇业', '闭店', '休息日', '停业'])
    stat['地址式样'] = len(addr_hits)
    stat['营业状态'] = '有' if has_status else '无'
    if len(addr_hits) >= 3 and not has_status:
        bad.append("⑩ 检出 %d 处地址但全文无营业状态说明——收录店须核过「营业中/暂停营业/歇业」"
                   "并写明；遇到暂停营业要说明换哪家分店" % len(addr_hits))

    # ⑪ 动线（粗查：有无"回头路"字样说明）
    if '动线' not in text and '路线' not in text:
        bad.append("⑪ 无动线描述")

    # ⑫ 预算
    has_budget = '预算' in text
    has_hidden = any(k in text for k in ['过路费', '停车费', '观光车', '托运', '讲解费', '服务费'])
    stat['预算'] = '有' if has_budget else '无'
    if has_budget and not has_hidden:
        bad.append("⑫ 预算无隐藏成本行——过路费/停车费/观光车/托运/讲解费须单列")

    # ⑬ 避雷
    bad_avoid = re.findall(r'注意防晒|注意安全|注意保暖|合理安[排排]', text)
    stat['空洞避雷'] = len(bad_avoid)
    if bad_avoid:
        bad.append("⑬ 避雷表有空洞条目 %d 处——须写成「真实场景+应对」式"
                   "（不好：「注意防晒」；好：「遗址区无遮挡，观光车必坐否则热死」）" % len(bad_avoid))

    # ⑭ 应急
    if 'Plan B' not in text and '备选' not in text and '应急' not in text:
        bad.append("⑭ 无应急方案——每个关键环节需 Plan B")

    # ⑮ 六维度齐全
    dims = {'吃': ['吃', '美食', '餐'], '玩': ['玩', '景点', '游览'],
            '住': ['住', '住宿', '酒店', '民宿'], '行': ['行', '交通', '车次'],
            '拍': ['拍', '机位', '摄影'], '避雷': ['避雷', '提示', '注意']}
    missing_dims = [d for d, kws in dims.items() if not any(k in text for k in kws)]
    stat['缺失维度'] = missing_dims
    if missing_dims:
        bad.append("⑮ 六维度缺失：%s" % '/'.join(missing_dims))

    # ⑯ 餐行机械钩：逐日早/午/晚
    day_blocks = _split_days(text)
    stat['日程块数'] = len(day_blocks)
    if not day_blocks:
        warn_items.append("⑯ 未识别到日程分块（无 Day/D1/第X天 形式的标题）——餐行检查跳过")
    else:
        incomplete_days = []
        for i, blk in enumerate(day_blocks, 1):
            has_morning = any(k in blk for k in ['早', '早餐'])
            has_noon = any(k in blk for k in ['午', '午餐', '中餐'])
            has_evening = any(k in blk for k in ['晚', '晚餐', '宵夜'])
            if not (has_morning and has_noon and has_evening):
                miss = []
                if not has_morning:
                    miss.append('早')
                if not has_noon:
                    miss.append('午')
                if not has_evening:
                    miss.append('晚')
                incomplete_days.append("Day%d缺%s" % (i, '/'.join(miss)))
        stat['餐行不全天数'] = len(incomplete_days)
        if incomplete_days:
            bad.append("⑯ 餐行不全：%s——每日必须有早/午/晚三行，「园内解决」也要有行" % '、'.join(incomplete_days))
        if days and len(day_blocks) != days:
            warn_items.append("⑯ 识别到 %d 个日程块，与 --days %d 不符——确认 Day 标题格式"
                              % (len(day_blocks), days))

    # 命名口径
    if '吃饭清单' in text:
        bad.append("· 板块名用了「吃饭清单」——固定写「美食清单」（grep 吃饭清单须为 0）")

    # ---- D 组：口径 ----
    # ㉖ 去 AI 味
    ai_hits = {}
    scan_text = text
    for pat in AI_WORD_EXCEPTIONS:
        scan_text = re.sub(pat, '', scan_text)
    for w in AI_WORDS:
        n = len(re.findall(w, scan_text))
        if n:
            ai_hits[w] = n
    stat['AI味词'] = ai_hits
    if ai_hits:
        bad.append("㉖ 检出 AI 味词 %s——像朋友交代攻略的口吻，不是旅游杂志文案"
                   % '、'.join('%s×%d' % (k, v) for k, v in ai_hits.items()))

    # ---- E 组：情报覆盖 ----
    # ㉗ 社会情报已声明
    # 判据 = 「留痕」而不是「必须采到」。情报采集是默认动作，但允许跳过——
    # 只要写清是「没采到」还是「不去采」。**报「没有」比报假的「有」值钱**，
    # 所以声明跳过同样算过；真正要拦的是**静默跳过**（那会让这条能力退回可选项）。
    intel_skipped = [k for k in INTEL_SKIPPED if k in text]
    intel_declared = [k for k in INTEL_DECLARED if k in text]
    intel_paths = [k for k in INTEL_PATHS if k in text]
    if intel_skipped:
        stat['社会情报'] = '已声明未采集'
        stat['情报路径'] = '—'
    elif intel_declared:
        stat['社会情报'] = '已声明'
        stat['情报路径'] = '/'.join(intel_paths) or '未写路径'
        if not intel_paths:
            warn_items.append("㉗ 写了情报来源但没写走哪条路径——补「官方文旅发布 / "
                              "搜索引擎索引 / 用户投喂」之一，否则下次没法按路径复采")
    else:
        stat['社会情报'] = '未声明'
        stat['情报路径'] = '—'
        bad.append("㉗ 全文没有社会情报声明——情报采集是默认动作（阶段 3.5）。"
                   "采了就写「情报来源」+ 走哪条路径 + 提了几条；"
                   "没采就写「本次未采集社会情报」+ 理由。**跳过可以，静默跳过不行**")

    # ---- B 组（续）：路线结构 ----
    # ㉘㉙ 只在 HTML 交付路径上查：判据是**渲染器注入的 data 属性**，不是散文关键词。
    # 结构性要求就该用结构判——事实源给了 routes/nearby，渲染器必然带出这些属性；
    # 没给就一定没有，不存在「写了但词不达意」的误判空间。md 是中间产物、没有注入，
    # 回退去查只会误报，所以直接跳过。
    if raw_html:
        # ㉘ 主路线 + 备选路线齐备
        primary_hits = len(re.findall(r'data-route="primary"', raw_html))
        alt_hits = re.findall(
            r'data-route="alternatives"[^>]*data-alt-count="(\d+)"', raw_html)
        alt_count = int(alt_hits[0]) if alt_hits else 0
        stat['主路线'] = '有' if primary_hits else '无'
        stat['备选路线数'] = alt_count
        if not primary_hits:
            bad.append("㉘ 没有主路线块——事实源缺 routes.primary。"
                       "备选给得再多，也得先有一条说清「今天就走这条」的主线")
        if alt_count < 2:
            bad.append("㉘ 备选路线只有 %d 条（要求 ≥2，或根本没给 routes.alternatives）——"
                       "备选可以简略，但必须给出，否则用户无从对比方案" % alt_count)

        # ㉙ 沿途顺道点已声明
        # 用 WARN 不用 BLOCK：主线上确实可能一处可顺道的都没有（沿线全是封闭景区，
        # 或目的地本身就是单点）。那种情况要的是**写明原因**，不是硬凑几个点出来。
        nb_hits = re.findall(
            r'data-nearby="1"[^>]*data-nearby-count="(\d+)"', raw_html)
        nb_count = int(nb_hits[0]) if nb_hits else 0
        stat['沿途顺道点数'] = nb_count
        if nb_count < 1:
            warn_items.append("㉙ 没给「沿途顺道可看」的点（事实源 days[].nearby）。"
                              "主线上确实没有可顺道进去的地方时，也请写明原因，别静默略过")

        # ㉚ 个性化偏好已声明
        # 用 WARN 不用 BLOCK：偏好是软字段（intake 缺省进 assumptions），没采集不拦。
        # 判据同样是渲染注入的 data 属性——给了 preferences 就带 data-preferences。
        pref_hits = len(re.findall(r'data-preferences="1"', raw_html))
        stat['个性化偏好'] = '有' if pref_hits else '无'
        if not pref_hits:
            warn_items.append("㉚ 没呈现「按你的偏好排的」块（事实源 preferences）。"
                              "阶段 0 若采集了用户自报的饮食/作息/兴趣偏好，就该在主路线区呈现；"
                              "没采集则无需补（软字段，不追问）")
    else:
        stat['路线结构'] = 'md 阶段不查（判据在渲染注入的 data 属性上）'

    return bad, warn_items, stat


# ============================================================ md 模式
def check_md(path, days, text):
    """md 事实源：共用内容检查 + ⑱(声明式配图计数)"""
    bad, warn_items, stat = check_content(text, days)

    # ---- C 组：交付物（md 阶段只查配图张数声明） ----
    nfig_decl = len(re.findall(r'<figure', text))
    stat['md内figure'] = nfig_decl
    if days and nfig_decl and nfig_decl < days:
        bad.append("⑱ md 内 figure %d 张 < 天数 %d" % (nfig_decl, days))
    elif days and not nfig_decl:
        stat['配图声明'] = '无 <figure>（md 阶段可接受，交付物计数在 HTML 上查）'

    return bad, warn_items, stat


# ============================================================ HTML 模式
def check_html(path, days, text):
    """HTML 产物：去标签后跑共用内容检查，再叠加 C 组产物检查"""
    # —— A/B/D 组：先还原成纯文本再查（本次新增，原实现完全没跑这一段）
    body, meta = html_to_text(text)
    # raw_html 一并传下去：徽章数只能数**原始** HTML——
    # 剥完标签后 class 属性就没了，第 ② 项会误判成「全篇零等级标」。
    bad, warn_items, stat = check_content(body, days, extra_sourced=meta.get('表格数值'),
                                          raw_html=text)
    stat['正文长度'] = len(body)
    if meta.get('无来源表格'):
        bad.append("① 有 %d 个表格整表回溯不到来源/时戳——表内里程/时长/票价须能追到"
                   "「哪来的、什么时候查的」（表下一行来源声明即可）" % meta['无来源表格'])

    # ---- C 组：产物检查（对原始 HTML 文本做，需要标签结构） ----

    # ⑱ 每日配图齐全
    nfig = text.count('<figure')
    stat['figure数'] = nfig
    if days and nfig < days:
        bad.append("⑱ 每日配图 %d 张 < 天数 %d——须逐日配图，一张都不能少" % (nfig, days))
    elif days:
        stat['配图达标'] = '%d ≥ %d 天' % (nfig, days)

    # ⑱ 非实拍配图必须标「（示意）」+ 页脚注明图源
    #    （原先这条被错标成 ⑲；SKILL.md 第 19 项是「逐张亲验」，属人工项）
    has_source_note = any(k in text for k in ['图源', '示意', 'AI 生成', 'AI生成'])
    stat['图源标注'] = '有' if has_source_note else '无'
    if nfig > 0 and not has_source_note:
        bad.append("⑱ 配图无来源标注——非实拍须标「（示意）」+页脚注明图源")

    # ㉑ 入场动效两态
    has_js_gate = "classList.add('js')" in text or 'classList.add("js")' in text
    has_fallback = 'setTimeout' in text
    has_bare_opacity = bool(re.search(r'opacity\s*:\s*0[^.\d]', text))
    stat['js门控'] = '有' if has_js_gate else '无'
    stat['兜底脚本'] = '有' if has_fallback else '无'
    if has_bare_opacity and not has_js_gate:
        bad.append("㉑ 裸 opacity:0 且无 html.js 门控——JS 挂掉会导致内容不可见")
    if has_bare_opacity and has_js_gate and not has_fallback:
        bad.append("㉑ 有门控无兜底脚本——需 setTimeout 兜底，防 IntersectionObserver 失效")

    # ---- 附加检查（不在 26 项之内，模板契约要求） ----
    features = {'字号+': 'fontInc', '字号−': 'fontDec', '打印': 'printBtn',
                '返回顶部': 'backtop', '滚动入场': 'IntersectionObserver'}
    missing_feat = [d for d, m in features.items() if m not in text]
    stat['缺交互'] = missing_feat
    if missing_feat:
        bad.append("· [附加] 缺交互功能：%s" % '/'.join(missing_feat))

    if 'viewport' not in text:
        bad.append("· [附加] 缺 viewport meta——手机上看会缩成一坨")

    return bad, warn_items, stat


# ============================================================ 覆盖声明
def coverage_lines(mode, skipped):
    """输出「本次实检 N/M」——防止把「全绿」读成「M 项全过」"""
    block = [x for x in ITEMS if x[3] == 'BLOCK' and x[0] not in skipped]
    warn = [x for x in ITEMS if x[3] == 'WARN']
    cond = [x for x in ITEMS if x[3] == 'COND']
    man = [x for x in ITEMS if x[3] == 'MANUAL']
    done = len(block) + len(warn)
    total = len(ITEMS)           # 不写死：加项时这里必须跟着变，否则覆盖声明会说谎
    L = []
    L.append("覆盖声明　本次实检 %d/%d 项（阻断级 %d ｜ 提示级 %d）"
             % (done, total, len(block), len(warn)))
    L.append("  ⚠️ 「全绿」只代表脚本能查的这 %d 项没问题——**不等于 %d 项全过**。"
             % (done, total))
    other = [x for x in cond + man] + [x for x in ITEMS if x[0] in skipped]
    if other:
        L.append("  本次未覆盖 %d 项，逐条说明：" % len(other))
        for num, grp, name, level, note in other:
            tag = {'COND': '条件项', 'MANUAL': '需人工', 'BLOCK': '未判定', 'WARN': '未判定'}[level]
            L.append("      %-3s %-18s [%s] %s" % (num, name, tag, note or ''))
    L.append("  该读法：脚本查得的项全绿，只说明「机械可查的那部分」没问题；")
    L.append("          上列各条必须人工过一遍，并在交付说明里如实写出「哪些检查没做」。")
    return L


# ============================================================ main
def main():
    ap = argparse.ArgumentParser(
        description='路书交付自查（30 项机械核对）。md 与 html 分开跑。')
    ap.add_argument('files', nargs='+')
    ap.add_argument('--days', type=int, default=None, help='行程天数')
    ap.add_argument('--html', action='store_true', help='按 HTML 产物检查')
    ap.add_argument('--json', action='store_true', help='输出 JSON（含覆盖声明）')
    ap.add_argument('--no-coverage', action='store_true', help='不打印覆盖声明')
    a = ap.parse_args()

    is_html = a.html or any(f.lower().endswith(('.html', '.htm')) for f in a.files)

    # --days 未给时 ⑱ 实际未判定，覆盖声明要如实扣除
    skipped = set() if a.days else {'⑱'}

    results = []
    for f in a.files:
        text = read(f)
        if is_html:
            bad, warn_items, stat = check_html(f, a.days, text)
        else:
            bad, warn_items, stat = check_md(f, a.days, text)
        results.append({'file': os.path.basename(f), 'bad': bad,
                        'warn': warn_items, 'stat': stat})

    cov = coverage_lines('html' if is_html else 'md', skipped)

    if a.json:
        emit(json.dumps({'results': results, 'coverage': cov}, ensure_ascii=False, indent=2))
        if any(r['bad'] for r in results):
            sys.exit(1)
        return

    emit("=" * 100)
    emit("路书交付自查　%s" % ('HTML 产物' if is_html else 'md 事实源'))
    emit("=" * 100)
    for r in results:
        status = "✅ 通过" if not r['bad'] else "❌ %d 项未过" % len(r['bad'])
        emit("\n%s  %s" % (status, r['file']))
        if r['stat']:
            emit("   " + " ｜ ".join("%s=%s" % (k, v) for k, v in r['stat'].items()
                                    if not isinstance(v, (dict, list)) or v))
        for x in r['bad']:
            emit("   - " + x)
        for x in r.get('warn', []):
            emit("   ~ " + x)

    if not a.no_coverage:
        emit("\n" + "-" * 100)
        for line in cov:
            emit(line)
        emit("-" * 100)

    emit("\n" + "=" * 100)
    total_bad = sum(len(r['bad']) for r in results)
    total_warn = sum(len(r.get('warn', [])) for r in results)
    if total_bad:
        emit("❌ 阻断项 %d 项未通过　—　全绿才可交付" % total_bad)
        if total_warn:
            emit("   （另有 %d 项提示，需人工确认）" % total_warn)
        sys.exit(1)
    if total_warn:
        emit("✅ 机械可查部分无阻断项　—　可交付（%d 项提示请人工确认）" % total_warn)
    else:
        emit("✅ 机械可查部分全部通过　—　可交付")

    if any(r['bad'] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
