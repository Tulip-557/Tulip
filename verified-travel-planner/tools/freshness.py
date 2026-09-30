#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
freshness.py — 时效体检：这份路书里的动态数字，到出发那天还新吗？

为什么需要它
────────────────────────────────────────────────────────────────────────
`references/evidence-rules.md` 写了一张 6 行的时效阈值表（机票 2h / 酒店 12h /
火车票 24h / 门票当天 / 营业时间 30d / 换乘当日），但**那张表只写给人看**：

· 事实源里只有 `meta.social_intel.checked_at` 一处时戳 —— 分类型的时戳**根本没落到数据上**
  （2026-09-27 实测：中山事实源全文 0 处 `source_checked_at`、0 处 `opening_time`）；
· `feasibility.py` 只查**行程层**单个 `source_checked_at`，而且写死 24 小时，
  管不到「门票当天有效」「营业时间 30 天」这些分级阈值；
· 于是「这份路书在出发那天还有几个数字是新的」，只能靠人肉回忆。

判据写得再漂亮，没有机器闸门就等于没有。本工具把那张表变成可执行判据。

它判什么
────────────────────────────────────────────────────────────────────────
扫事实源正文里**带来源时戳的动态声明**，按类型套阈值，以**出发日**为基准：

    「门票：日夜票 ¥39.9（携程，2026-09-27）」  → 门票价格，阈值当天
    出发日是 2026-09-28                          → 距今 1 天 > 0     → 须重查

同时报出**疑似动态但没有时戳**的声明 —— 那种连「新不新」都无从判断，
按铁律 1「每个数字都要有来源与时戳」，它比过期更值得点名。

口径（与 `ludbook_check` 第 ① 项的教训一致）
────────────────────────────────────────────────────────────────────────
「票价」指**门票 / 车票 / 机票 / 房价**，**不含小吃、人均餐费、茶位费**。
中山实测：若把「人均 ¥40」也当票价，一次跑出 55 处噪声 —— 噪声大的闸门一定会被绕过。
本工具的 KINDS 只认强信号词，且要求声明里出现**具体金额或时长**。

只认「有时戳的」需要先过**来源词过滤**：日期前后 40 字符内必须出现
「查 / 核 / 携程 / 高德 / 公众号 / 官方…」，否则那个日期只是行程日期（如 2026-09-28），
不是查询时戳。这一条把中山那 74 处裸日期里的行程日期全部排除。

双档声明只扫 `full` 档（2026-09-27 加）
────────────────────────────────────────────────────────────────────────
事实源的 `sections` / `days[].slots` 可写 `{"full": …, "compact": …}` 双档
（见 `references/dual-version.md`）。本工具**只扫 `full` 档**：compact 是同一句的
改写、数字须与 full 一致（由 `compact_check` 的 C3「约束不丢」保证），
两档都扫等于同一条声明报两遍。实测加上双档后须重查从 3 条虚增到 6 条，全是重复。
只写了 `compact`、没写 `full` 的 dict 仍照扫，**不许漏声明**。

用法
────────────────────────────────────────────────────────────────────────
    python freshness.py 路书_XX_事实源.json                    # 基准日取 meta.date_range 起始
    python freshness.py 路书_XX_事实源.json --on 2026-09-28     # 显式指定基准日
    python freshness.py 路书_XX.html --on 2026-09-28 --json
    python freshness.py 路书_XX_事实源.json --strict            # 有过期项则退出码 2

退出码
────────────────────────────────────────────────────────────────────────
0 = 没有须重查项（或有但未开 --strict）
2 = --strict 且存在须重查项
1 = 输入读不到 / 解析失败
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# ---- 终端符号自适应（中文 Windows 黑框认不出 ✅ 时退 ASCII），与 ludbook_check 同款
_SYM_FALLBACK = str.maketrans({
    '✅': '[OK]', '✓': '[OK]', '√': '[OK]',
    '❌': '[X]', '✗': '[X]', '×': '[X]',
    '→': '->', '■': '[*]', '●': '[*]', '·': '.',
    '⚠': '(!)', '🔴': '(*)', '｜': '|', '─': '-', '—': '-',
    'ℹ': '[i]',
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


# ──────────────────────────── 时效阈值表（唯一出处：references/evidence-rules.md） ──
# 字段：id / 中文名 / 阈值小时 / 判定正则 / 分组
#
# 分组是**呈现口径**，不是判据松紧 —— 阈值照旧一律执行，只是分开报：
#   EXTERNAL = 外部事实（票价、营业时间）。过期会直接误导读者 → 逐条点名，--strict 看这一组。
#   ROUTE    = 行程推导（车程、距离）。来源本就是地图，出发当天批量重算即可
#              → 折叠成一行。中山实测若逐条列，12 条路线会把 3 条真要紧的票价淹掉
#              —— 「噪声大的闸门一定会被绕过」是本项目的既有教训。
#
# 顺序即优先级：同一段声明命中多类时，取**阈值更严**的那条（宁可多提醒）。
# 阈值单位 = 小时。0 表示「必须与基准日同一天」。
KINDS = [
    ('TICKET_PRICE', '门票价格', 0, 'EXTERNAL',
     r'门票|票价|入场券|观光车票|索道票|讲解费|日夜票|全价票|半价票|船票|摆渡票'),
    ('FLIGHT', '机票价格', 2, 'EXTERNAL',
     r'机票|航班|经济舱|商务舱|直飞|转机|往返含税'),
    ('LODGING', '酒店房价', 12, 'EXTERNAL',
     r'房价|大床房|双床房|标间|每晚|含早|不含早|入住|退房|民宿价'),
    ('RAIL', '火车票', 24, 'EXTERNAL',
     r'二等座|一等座|商务座|无座|余票|车次|高铁|动车|城际|[GDC]\d{2,4}\s*次'),
    ('HOURS', '营业时间', 720, 'EXTERNAL',
     r'开放时间|营业时间|闭馆|闭园|停止入场|末班|售票时间|入场截止|预约截止'),
    ('ROUTE', '换乘路线', 0, 'ROUTE',
     r'\d+(?:\.\d+)?\s*公里|车程|步行约|骑行约|打车约|自驾约'),
]
_KIND_BY_ID = {k[0]: k for k in KINDS}

# 汇总结论句不是「外部事实声明」，而是本趟自己的推导 —— 别让它污染判定。
# 中山实测：「预算 ¥2,365 两人合计 ¥1,183 人均 ¥600 住宿（2 晚…」曾被判成须重查的票价。
_SUMMARY_RE = re.compile(r'预算|合计|总计|人均|小计|总价|花销|费用统计')

# 声明里必须出现「具体数值」才算动态数字 —— 挡住「建议早点去」这类定性句
# 以及「开放时间以现场为准」这类无数字的提醒。
_VALUE_RE = re.compile(
    r'[¥￥]\s*\d'                       # 金额
    r'|\d+(?:\.\d+)?\s*元'              # 元
    r'|\d+(?:\.\d+)?\s*公里'            # 距离
    r'|\d+\s*(?:分钟|小时|min)'         # 时长
    r'|\d{1,2}\s*[:：]\s*\d{2}'         # 时刻
    r'|\d{1,2}\s*点'                    # 点钟
)

# 「无时戳」这一组要用**更窄**的判据：只认「含量额」或「含具体营业时段」的句子。
# 为什么：第一版用 _VALUE_RE，把「今日交通：珠海→中山城际，加中山北站打车 21 分钟」
# 这种纯行程叙述也收了进来 —— 中山实测 10 条里 5 条是这类误报。
# 放宽的判据只会让人忽略整组输出。
_UNDATED_VALUE_RE = re.compile(
    r'[¥￥]\s*\d'                                                   # 金额
    r'|\d+(?:\.\d+)?\s*元'                                          # 元
    r'|\d{1,2}\s*[:：]\s*\d{2}\s*[–—~\-至]\s*\d{1,2}\s*[:：]\s*\d{2}'  # 营业时段 9:00–17:30
)

# 来源词 —— 日期前后窗口里必须命中一个，否则那只是行程日期不是查询时戳
_SOURCE_WORDS = (
    '查', '核', '据', '来源', '携程', '高德', '美团', '点评', '马蜂窝', '公众号',
    '官方', '官网', '同程', '飞猪', '永安', '去哪儿', 'App', '小程序',
    'B站', '微博', '小红书', '抖音', '知乎', '维基', 'OSM', 'OpenStreetMap',
    'OA', 'OTA', '铁路', '12306', '客服', '电话', '预订', '开放平台',
)

_DATE_RE = re.compile(r'(20\d{2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})'
                      r'(?:\s+(\d{1,2})\s*[:：]\s*(\d{2}))?')

# 声明切分：把长段落切成便于判定的小单元
_SPLIT_RE = re.compile(r'<br\s*/?>|</p>|</li>|\n|。|；|;')
_TAG_RE = re.compile(r'<[^>]+>')


def strip_tags(s: str) -> str:
    return _TAG_RE.sub(' ', s)


# ──────────────────────────── 输入读取 ────────────────────────────
def collect_fragments(path: Path):
    """把输入统一成「若干文本片段」+ 元信息。

    JSON 事实源：递归收集所有字符串值（含数组元素）。
    HTML 产物：剥 <script>/<style> 后收集全部文本。
    """
    raw = path.read_bytes()
    text = raw.decode('utf-8', 'replace')
    meta = {}
    frags = []

    if path.suffix.lower() == '.json':
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise SystemExit('事实源不是合法 JSON：%s' % e)
        m = data.get('meta') or {}
        meta = {
            'destination': m.get('destination'),
            'date_range': m.get('date_range'),
            'verified_date': m.get('verified_date'),
        }

        def walk(node):
            if isinstance(node, str):
                frags.append(node)
            elif isinstance(node, dict):
                # 双档声明 {"full": …, "compact": …}（见 references/dual-version.md）：
                # **只扫 full 档**。compact 是同一句的「换写法」，数字必须与 full 一致
                # （compact_check 的 C3「约束不丢」就是管这个）——两档都扫等于同一条声明
                # 报两遍。2026-09-27 实测：slots 加双档后，须重查从 3 条虚增到 6 条，
                # 全是重复。**噪声大的闸门一定会被绕过**，是本项目的既有教训。
                # 反例兜底：只写了 compact、没写 full 的 dict 仍照扫，不许漏声明。
                if 'full' in node and 'compact' in node:
                    walk(node['full'])
                else:
                    for v in node.values():
                        walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(data)
    else:
        body = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', text)
        frags.append(body)
        # HTML 没有 meta.date_range，得从骨架自己恢复行程首日。
        # 为什么必须做：恢复不了就会静默回退到「本机今天」——而今天往往正是写这份路书的那天，
        # 于是**全部判成新鲜**。实测踩到：中山 HTML 报「须重查 0 条」，JSON 却报 3 条，
        # 差别只在本机今天(9/27)恰好等于时戳日。这是最典型的一种假绿。
        for pat in (r'DAY\s*1\s*/\s*(20\d{2}-\d{1,2}-\d{1,2})',
                    r'day-idx[^>]*>\s*DAY\s*1\s*/\s*(20\d{2}-\d{1,2}-\d{1,2})',
                    r'(20\d{2}-\d{1,2}-\d{1,2})\s*[~～—-]\s*20\d{2}-\d{1,2}-\d{1,2}'):
            m = re.search(pat, body)
            if m:
                meta['date_range'] = m.group(1)
                meta['date_range_from'] = 'HTML 骨架 day-idx' if 'DAY' in pat else 'HTML 正文日期区间'
                break

    return frags, meta


def base_date_from_meta(meta: dict):
    """基准日缺省 = meta.date_range 的起始日（出发日）。"""
    dr = str(meta.get('date_range') or '')
    src = meta.get('date_range_from') or 'meta.date_range 起始日'
    m = _DATE_RE.search(dr)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), src
        except ValueError:
            pass
    vd = str(meta.get('verified_date') or '')
    m = _DATE_RE.search(vd)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), 'meta.verified_date'
        except ValueError:
            pass
    return None, None


# ──────────────────────────── 判定 ────────────────────────────
def parse_stamp(m) -> datetime:
    """把正则匹配变成 datetime；无时刻时按当日 12:00 计（比 00:00 更宽容）。"""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh = int(m.group(4)) if m.group(4) else 12
    mm = int(m.group(5)) if m.group(5) else 0
    return datetime(y, mo, d, hh, mm)


def classify(unit: str):
    """判这段声明的类型；命中多类时取阈值最严的。返回 dict 或 None。"""
    if _SUMMARY_RE.search(unit):        # 汇总/推导句，不是外部事实声明
        return None
    hits = []
    for kid, name, hours, group, pat in KINDS:
        if re.search(pat, unit):
            hits.append((hours, kid, name, group))
    if not hits:
        return None
    hits.sort(key=lambda x: x[0])          # 阈值小的（更严）排前
    hours, kid, name, group = hits[0]
    return {'kind': kid, 'name': name, 'hours': hours, 'group': group,
            'also': [h[2] for h in hits[1:]]}


def has_source_near(text: str, pos: int, span: int = 40) -> bool:
    """日期附近有没有来源词。没有 → 那只是行程日期，不是查询时戳。"""
    win = text[max(0, pos - span): pos + span]
    return any(w in win for w in _SOURCE_WORDS)


def scan(frags, base: date):
    dated, undated = [], []
    seen_dated = set()

    for frag in frags:
        if not frag or not frag.strip():
            continue
        plain = strip_tags(frag)
        for unit in _SPLIT_RE.split(plain):
            unit = unit.strip()
            if len(unit) < 6 or not _VALUE_RE.search(unit):
                continue

            stamps = [m for m in _DATE_RE.finditer(unit) if has_source_near(unit, m.start())]
            if not stamps:
                # 有数值、有动态信号、却没有可识别的来源时戳。
                # 只收 EXTERNAL 组 —— 路线类的来源本就是地图，没写时戳不算漏；
                # 票价类没时戳才是真问题（违反铁律 1「每个数字都要有来源与时戳」）。
                kind = classify(unit)
                if kind and kind['group'] == 'EXTERNAL' and _UNDATED_VALUE_RE.search(unit):
                    key = (kind['kind'], unit[:60])
                    if key not in seen_dated:
                        seen_dated.add(key)
                        undated.append({'kind': kind['kind'], 'name': kind['name'],
                                        'text': unit[:160]})
                continue

            kind = classify(unit)
            if not kind:
                continue

            for m in stamps:
                stamp = parse_stamp(m)
                age_h = (datetime(base.year, base.month, base.day) - stamp).total_seconds() / 3600
                stale = age_h > kind['hours']
                key = (kind['kind'], stamp.date().isoformat(), unit[:60])
                if key in seen_dated:
                    continue
                seen_dated.add(key)
                dated.append({
                    'kind': kind['kind'], 'name': kind['name'], 'group': kind['group'],
                    'text': unit[:160], 'stamp': stamp.isoformat(sep=' '),
                    'stamp_date': stamp.date().isoformat(),
                    'age_hours': round(age_h, 1),
                    'threshold_hours': kind['hours'],
                    'verdict': 'MUST_RECHECK' if stale else 'FRESH',
                    'also': kind['also'],
                })

    dated.sort(key=lambda r: (r['verdict'] != 'MUST_RECHECK', -r['age_hours']))
    return dated, undated


def threshold_label(hours: int) -> str:
    if hours == 0:
        return '当天'
    if hours < 24:
        return '%dh' % hours
    if hours == 24:
        return '24h'
    return '%dd' % (hours // 24)


# ──────────────────────────── 输出 ────────────────────────────
def render_text(path, base, base_src, dated, undated, strict, verbose=False):
    ext_stale = [r for r in dated if r['verdict'] == 'MUST_RECHECK' and r['group'] == 'EXTERNAL']
    rt_stale = [r for r in dated if r['verdict'] == 'MUST_RECHECK' and r['group'] == 'ROUTE']
    fresh = [r for r in dated if r['verdict'] == 'FRESH']

    emit('【时效体检】%s' % path.name)
    emit('基准日 %s（%s）' % (base.isoformat(), base_src or '命令行指定'))
    emit('─' * 78)

    if ext_stale:
        emit('')
        emit('⚠️ 须重查的外部事实 %d 条 —— 这些数字在出发日已超出时效，进路书前必须回原渠道复核：'
             % len(ext_stale))
        emit('')
        emit('  %-10s %-12s %-8s %-6s %s' % ('类型', '时戳', '距今', '阈值', '声明'))
        for r in ext_stale:
            emit('  %-10s %-12s %-8s %-6s %s'
                 % (r['name'], r['stamp_date'], '%sh' % r['age_hours'],
                    threshold_label(r['threshold_hours']), r['text'][:48]))
    else:
        emit('')
        emit('✅ 外部事实（票价 / 营业时间）无一条超出时效。')

    if rt_stale:
        emit('')
        emit('📌 路线时长 %d 条待出发日重算（阈值「出行当日」，例行动作，不算错）：' % len(rt_stale))
        kinds = {}
        for r in rt_stale:
            kinds[r['name']] = kinds.get(r['name'], 0) + 1
        emit('   %s —— 出发当天回高德跑一次 amap-snapshot 即可整批刷新。'
             % '｜'.join('%s %d 条' % (k, v) for k, v in kinds.items()))
        if verbose:
            for r in rt_stale:
                emit('   · [%s] %s' % (r['name'], r['text'][:62]))

    if fresh:
        emit('')
        emit('✅ 仍在时效内 %d 条：' % len(fresh))
        for r in fresh[:12]:
            emit('  · %s（%s，阈值 %s）：%s'
                 % (r['name'], r['stamp_date'], threshold_label(r['threshold_hours']),
                    r['text'][:52]))
        if len(fresh) > 12:
            emit('  … 另有 %d 条' % (len(fresh) - 12))

    if undated:
        emit('')
        emit('ℹ️ 票价 / 营业时间 但查不到时戳 %d 条 —— 连「新不新」都无从判断：' % len(undated))
        for r in undated[:10]:
            emit('  · [%s] %s' % (r['name'], r['text'][:64]))
        if len(undated) > 10:
            emit('  … 另有 %d 条（用 --json 看全量）' % (len(undated) - 10))

    emit('')
    emit('─' * 78)
    emit('汇总：带时戳的动态声明 %d 条 ｜ 外部事实须重查 %d ｜ 路线待重算 %d ｜ 仍在时效内 %d ｜ 无时戳 %d'
         % (len(dated), len(ext_stale), len(rt_stale), len(fresh), len(undated)))
    if not dated and not undated:
        emit('ℹ️ 这条路径没有扫到动态数字 —— 注意这只说明「没扫到」，不等于「都新鲜」。')
    emit('')
    emit('阈值口径见 references/evidence-rules.md；本工具只判「新不新」，不判「对不对」。')
    emit('须重查不等于错 —— 它是「出发前记得再打一通电话 / 再刷一次票」的清单。')

    return 2 if (strict and ext_stale) else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description='时效体检：动态数字到出发日还新不新（按 evidence-rules 的 6 类阈值）')
    ap.add_argument('input', help='事实源 JSON，或渲染后的 HTML')
    ap.add_argument('--on', help='基准日 YYYY-MM-DD，缺省取 meta.date_range 起始日')
    ap.add_argument('--strict', action='store_true',
                    help='外部事实（票价/营业时间）有须重查项时退出码 2；路线类不计入')
    ap.add_argument('--verbose', '-v', action='store_true', help='展开逐条路线明细')
    ap.add_argument('--json', action='store_true', help='输出机器可读 JSON')
    a = ap.parse_args()

    path = Path(a.input)
    if not path.exists():
        emit('❌ 读不到文件：%s' % path)
        return 1

    frags, meta = collect_fragments(path)

    if a.on:
        try:
            base = datetime.strptime(a.on.strip(), '%Y-%m-%d').date()
        except ValueError:
            emit('❌ --on 需要 YYYY-MM-DD，收到：%s' % a.on)
            return 1
        base_src = '命令行 --on'
    else:
        base, base_src = base_date_from_meta(meta)
        if base is None:
            base = date.today()
            base_src = ('本机今天 —— ⚠️ 文件里没找到行程日期，无法按出发日判定；'
                        '若这趟不是今天出发，请显式给 --on')

    dated, undated = scan(frags, base)
    ext_stale = [r for r in dated if r['verdict'] == 'MUST_RECHECK' and r['group'] == 'EXTERNAL']

    if a.json:
        out = {
            'input': str(path),
            'destination': meta.get('destination'),
            'base_date': base.isoformat(),
            'base_date_source': base_src,
            'dated': dated,
            'undated': undated,
            'summary': {
                'dated': len(dated),
                'external_must_recheck': len(ext_stale),
                'route_recheck_on_departure': sum(
                    1 for r in dated if r['verdict'] == 'MUST_RECHECK' and r['group'] == 'ROUTE'),
                'fresh': sum(1 for r in dated if r['verdict'] == 'FRESH'),
                'undated': len(undated),
            },
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 2 if (a.strict and ext_stale) else 0

    return render_text(path, base, base_src, dated, undated, a.strict, a.verbose)


if __name__ == '__main__':
    sys.exit(main())
