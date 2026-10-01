#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
travel_planner.py — 统一命令行入口（本 skill 的主入口）

为什么需要它
------------
`engine/travel_planner/` 是纯库，没有命令行入口。库装好了不等于能用——
使用者需要一个「敲一条命令就能跑」的入口，否则等于发动机搬来了却没装开关。
本文件补齐的就是这个开关。

迁移自 tanweiping1012-source/travel-planner（MIT）的 `scripts/travel_planner.py`，
做了七处适配（逐条与 `THIRD_PARTY_NOTICES.md` 的派生文件说明对应）：

  ① 引擎路径 `src/` → `engine/`（本 skill 的布局）
  ② 凭据用本 skill 的跨平台版（环境变量 → 凭据文件 → macOS 钥匙串）；
     上游用 `KeychainCredentialStore`，那个名字在 Windows 上取不到任何值
  ③ `doctor` 整合本机体检（tzdata / 随包资产 / 引擎 + 上游的 rail · browser）
     上游只报 rail 与 browser，量不到「这台机器上到底能不能跑」
  ④ `evaluate` 在 `INFEASIBLE` 时返回非零退出码。上游只打印报告就正常退出，
     调用方拿不到「排不通」这个信号——而「排不通不给你」是本 skill 的硬闸门，
     闸门必须能被程序感知
  ⑤ 新增 `nearby-spots` 命令（上游没有）：沿主路线各站走高德周边搜索，采
     「顺道可去」的候选点，供事实源的 `days[].nearby` 用。只列不判断
  ⑥ 新增来源留痕出口（2026-09-30，P1）：`amap-snapshot --no-keep-raw`、
     `search-places --trace` / `nearby-spots --trace`
  ⑦ 新增 `route` 命令（2026-10-01，P1 v2）：点对点走高德方向接口，把驾车/
     步行距离、过路费、公交票价采出来并留痕——此前没有任何命令能产出这三类
     数值，事实源里的「9.5 公里 / 过路费 ¥17」因此永远无留痕可对

命令一览
--------
    credential-status   看 key 配没配上、从哪读到的
    preflight           实测 key 能否真连高德（会联网）
    doctor              环境体检（本机能力 + 高德 + 铁路 + 浏览器）
    validate-request    需求采集校验（缺项 -> 追问，不猜）
    search-places       查高德 POI（需 key）
    nearby-spots        沿主路线各站采「顺道可去」的候选点（需 key；只列不判断）
    route               点对点距离/时长/过路费·票价（需 key；P1 来源留痕 v2）
    amap-snapshot       一次采集坐标 + 路线 + 周边（需 key）
    evaluate            确定性可行性检查（排不通不给你）
    compile-research    社区线索汇编成景点卡
    normalize-rail      12306 余票归一化（「有/无/数字」混合值）
    validate-flights    机票报价校验（2 小时新鲜度 + 时长一致性）
    validate-lodging    酒店报价校验（登录态 + 会员等级 + 每晚价推导总价）
    validate-plan       方案内容完整性（内连带跑机票/酒店校验）

退出码
------
    0 通过 ｜ 1 执行出错 ｜ 2 校验未通过（INVALID）｜ 3 证据不全（INCOMPLETE_EVIDENCE）

用法
----
    python travel_planner.py --help
    python travel_planner.py doctor --live
    python travel_planner.py evaluate --input itinerary.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = TOOLS_DIR.parent
ENGINE_DIR = SKILL_ROOT / 'engine'
sys.path.insert(0, str(ENGINE_DIR))

from travel_planner.amap import AmapClient, AmapError              # noqa: E402
from travel_planner.credentials import CredentialError, CredentialStore  # noqa: E402
from travel_planner.diagnostics import build_doctor_report         # noqa: E402
from travel_planner.feasibility import evaluate_itinerary          # noqa: E402
from travel_planner.flight import (                                # noqa: E402
    DEFAULT_MAX_AGE_HOURS as FLIGHT_MAX_AGE_HOURS,
    validate_offers as validate_flight_offers,
)
from travel_planner.intake import validate_trip_request            # noqa: E402
from travel_planner.lodging import (                               # noqa: E402
    DEFAULT_MAX_AGE_HOURS as LODGING_MAX_AGE_HOURS,
    validate_offers as validate_lodging_offers,
)
from travel_planner.models import Location, to_dict                 # noqa: E402
from travel_planner.rail import normalize_query_result, select_trains  # noqa: E402
from travel_planner.research import (                              # noqa: E402
    compile_destination_brief,
    validate_plan_content,
)
from travel_planner.weather import assess_forecast                 # noqa: E402
from travel_planner.workflow import collect_amap_snapshot          # noqa: E402

# 中文 Windows 终端是 GBK 码页，直接 print 中文括号里的符号会 UnicodeEncodeError
# 崩框。降级成 ASCII 再打一次，别让体检报告因为一个符号打不出来。
_SYM_FALLBACK = str.maketrans({
    '✅': '[OK]', '✓': '[OK]', '❌': '[X]', '✗': '[X]',
    '⚠': '[!]', '·': '.', '→': '->', '｜': '|',
})


def _emit_line(text: str = '') -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        try:
            print(text.translate(_SYM_FALLBACK))
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or 'ascii'
            print(text.encode(enc, 'replace').decode(enc))


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('--now 必须带时区偏移，如 2026-09-27T13:00+08:00')
    return parsed


def _read_json(path: str) -> dict:
    with Path(path).open('r', encoding='utf-8') as handle:
        return json.load(handle)


def _emit(data, output: str = None) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text + '\n', encoding='utf-8')
        _emit_line('已写入 %s' % output)
    else:
        _emit_line(text)


def _amap_client() -> AmapClient:
    return AmapClient(CredentialStore().get('amap'))


#: 周边搜索会混进培训机构、奶茶店、写字楼——人民广场那种商圈尤其多（实测：
#: 搜规划馆周边前 6 条里有 3 条是舞蹈教室和奶茶店）。这些不是「顺道可看的景点」，
#: 直接丢；但过滤条数要如实报出来，否则看起来像「附近就这么点东西」。
#: ⚠️ 过滤只是减噪，**挡不干净**（高德把不少小店也归到「风景名胜相关」），
#: 所以输出仍带 `category` 供人工过一眼——本命令只列，不替你判断值不值得去。
_NOISE_CATEGORY_HINTS = (
    '培训', '餐饮', '购物', '商店', '公司', '办公', '住宅', '酒店', '宾馆',
    '美发', '美容', '健身', '超市', '便利', '银行', '金融', '中介', '租赁',
)


def _is_noise_category(category) -> bool:
    text = str(category or '')
    return any(hint in text for hint in _NOISE_CATEGORY_HINTS)


def _haversine_m(lng1: float, lat1: float, lng2: float, lat2: float) -> int:
    """两点球面距离（米）。由**实测坐标**算得，属确定性推导，不是估算。"""
    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lng2 - lng1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return int(2 * radius * math.asin(math.sqrt(a)))


def _local_checks() -> list:
    """复用 tools/doctor.py 的本机检查，避免两处维护同一套判据。"""
    sys.path.insert(0, str(TOOLS_DIR))
    try:
        import doctor as _doc
        return [
            _doc.check_python(),
            _doc.check_tzdata(),
            _doc.check_engine(),
            _doc.check_assets(),
        ]
    except Exception as exc:                      # 单点失败不该拖垮整个体检
        return [{
            'item': '本机检查', 'status': 'MISSING', 'critical': False,
            'detail': '%s: %s' % (type(exc).__name__, exc),
        }]


# ============================ 各子命令 ============================
def command_credential_status(args: argparse.Namespace) -> None:
    _emit(CredentialStore().status('amap'))


def command_preflight(args: argparse.Namespace) -> None:
    _emit(_amap_client().preflight())


def command_doctor(args: argparse.Namespace) -> None:
    credential_store = CredentialStore()
    amap_status = credential_store.status('amap')
    if amap_status['status'] == 'CONFIGURED' and args.live:
        try:
            amap_status = _amap_client().preflight()
        except (CredentialError, AmapError, OSError) as exc:
            amap_status = {
                'provider': 'amap',
                'status': 'ERROR',
                'error_type': exc.__class__.__name__,
                'message': str(exc),
            }

    report = build_doctor_report(
        amap_status,
        browser_status=args.browser_status,
        client=args.client,
    )
    report['environment'] = _local_checks()
    _emit(report)

    # 核心能力缺失要让退出码说话，否则 CI / 编排层看不出来
    critical_bad = [r for r in report['environment']
                    if r.get('critical') and r.get('status') != 'READY']
    if critical_bad:
        raise SystemExit(1)


def command_validate_request(args: argparse.Namespace) -> None:
    report = validate_trip_request(_read_json(args.input))
    _emit(report, args.output)
    if report['status'] == 'INVALID':
        raise SystemExit(2)


def _write_trace(client, trace_path) -> None:
    """P1 来源留痕：把本次运行全部成功调用的原始返回体落盘（key 已脱敏）。

    声明比对（tools/claim_audit.py）靠这份留痕才能机械复核「标了 [A] 的数
    是否真来自高德」。留痕缺位时那些声明就永远停在「说得出来源」。
    """
    if not trace_path:
        return
    _emit({
        'provider': 'amap',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'call_count': len(client.call_log),
        'calls': client.call_log,
    }, trace_path)


def command_search_places(args: argparse.Namespace) -> None:
    client = _amap_client()
    places = client.search_places(args.keywords, args.city, args.limit)
    _emit({'places': [to_dict(place) for place in places]})
    _write_trace(client, args.trace)


def command_nearby_spots(args: argparse.Namespace) -> None:
    """沿主路线每一站采「顺道可去」的候选点（高德周边搜索）。

    定位：主路线定下来**之后**的补充动作。把每站附近、没排进主线的景点如实捞出来，
    交给人去筛（值不值得进、那天开不开）。本命令**只负责把范围内的东西列出来**，
    不替你判断值不值得去，也不产出任何未核实的时间或票价——那属于人的判断。

    输入（JSON）：
        {"city": "上海", "radius_meters": 1000, "limit": 8,
         "stops": [{"name": "外滩"}, {"name": "豫园", "location": "121.49,31.22"}]}
        `location` 给了就直接用；没给就用 search-places 找一次（吃关键字搜索配额，
        个人认证约 100 次/日，所以能带坐标就带）。

    输出：每站一个 `places` 数组，按距离升序；`distance_meters` 由实测坐标算得。
    """
    spec = _read_json(args.input)
    client = _amap_client()

    city = spec.get('city')
    radius = int(spec.get('radius_meters') or 1000)
    limit = int(spec.get('limit') or 8)
    types = spec.get('types') or '110000|140000'

    out_stops = []
    for raw in spec.get('stops') or []:
        if isinstance(raw, str):
            raw = {'name': raw}
        if not isinstance(raw, dict):
            continue
        name = str(raw.get('name') or '').strip()
        if not name:
            continue

        coord = str(raw.get('location') or '').strip()
        if ',' in coord:
            lng, lat = (float(x) for x in coord.split(',')[:2])
            center = Location(name=name, longitude=lng, latitude=lat, city=city)
        else:
            found = client.search_places(name, city, 1)
            if not found:
                out_stops.append({'stop': name, 'error': '未搜到该地点，跳过'})
                continue
            center = found[0].location

        places = client.search_around(
            center, types=types, radius_meters=radius, limit=limit)
        items = []
        skipped = 0
        for place in places:
            if place.name == name:
                continue
            if _is_noise_category(place.category):
                skipped += 1
                continue
            items.append({
                'name': place.name,
                'address': place.address,
                'category': place.category,
                'rating': place.rating,
                'location': '%s,%s' % (place.location.longitude, place.location.latitude),
                'distance_meters': _haversine_m(
                    center.longitude, center.latitude,
                    place.location.longitude, place.location.latitude),
            })
        items.sort(key=lambda item: item['distance_meters'])
        out_stops.append({
            'stop': name,
            'location': '%s,%s' % (center.longitude, center.latitude),
            'places': items,
            'filtered_as_noise': skipped,
        })

    _emit({
        'city': city,
        'checked_at': datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%dT%H:%M%z'),
        'provider': 'amap',
        'radius_meters': radius,
        'stops': out_stops,
    }, args.output)
    _write_trace(client, args.trace)


def _resolve_endpoint(client, raw, name, city) -> Location:
    """把一个端点解析成 Location：**坐标优先**，给名字才回退 search-places。

    坐标（`lng,lat`）是主通道——不吃关键字搜索配额（个人认证约 100 次/日），
    而且端点确定，不会再被高德的同名地点匹配换掉。给名字才多花一次搜索。
    """
    text = str(raw or '').strip()
    if not text:
        raise ValueError('端点为空——该段缺 origin 或 destination「%s」' % (name or '?'))
    if ',' in text:
        try:
            lng, lat = (float(x) for x in text.split(',')[:2])
        except ValueError:
            raise ValueError('坐标格式应为「经度,纬度」，收到「%s」' % text)
        return Location(name=str(name or text), longitude=lng, latitude=lat, city=city)
    found = client.search_places(text, city, 1)
    if not found:
        raise ValueError('未搜到地点「%s」（给坐标可绕过搜索、也更省配额）' % text)
    place = found[0].location
    return Location(name=str(name or place.name or text),
                    longitude=place.longitude, latitude=place.latitude,
                    city=place.city or city)


def command_route(args: argparse.Namespace) -> None:
    """点对点路线查询（高德方向接口）——距离 / 时长 / 过路费·票价，逐段落留痕。

    定位：**P1 来源留痕 v2 的采集侧**。在它之前，`distance_meters` /
    `estimated_cost` 这两类数值没有任何命令能产出来（`amap-snapshot` 只采
    城市级起终点，`search-places` / `nearby-spots` 都不调方向接口），
    于是事实源里的「9.5 公里」「过路费 ¥17」永远无留痕可对。本命令补这条通道。

    与 `amap-snapshot` 的分工（**两条通道别混用**）：
      · `amap-snapshot` 走**城市级**起终点，带覆盖门禁（防「查东京得广西村庄」）；
      · 本命令走**POI 级**点对点，与 `search-places` 同一档，不做行政级覆盖检查。
    门禁各自成立，混用会同时破坏两边——城市级查询必须走前者的覆盖检查。

    输入（JSON）：
        {"mode": "driving",              # driving / walking / transit
         "city": "东莞",                  # 可被每段的 city 覆盖；transit 必需
         "legs": [{"from_id": "a", "to_id": "b", "name": "虎门站",
                   "origin": "113.66,22.82", "destination": "113.75,22.90"}]}
        `origin` / `destination` 给坐标最省（`lng,lat`）；给名字则回退一次搜索。
        跨城公交可另给 `origin_city` / `destination_city`。

    输出：逐段 `duration_minutes` / `distance_meters` / `estimated_cost`
    （驾车 = 过路费 tolls，公交 = 票价 cost）/ `transfer_count` /
    `walking_distance_meters`。**单段失败只记进 `errors`，不拖垮整批**——
    批量采 14 段时，一条坏路不该把其余 13 段的实采值一起丢掉。
    """
    spec = _read_json(args.input)
    client = _amap_client()

    mode = str(spec.get('mode') or 'driving').strip().lower()
    if mode not in ('driving', 'walking', 'transit'):
        raise ValueError('mode 只能是 driving / walking / transit，收到「%s」' % mode)
    default_city = spec.get('city')

    legs_out, errors = [], []
    for raw in spec.get('legs') or []:
        if not isinstance(raw, dict):
            errors.append({'leg': str(raw), 'error': '该段不是对象，跳过'})
            continue
        label = '%s→%s' % (raw.get('from_id') or '?', raw.get('to_id') or '?')
        city = raw.get('city') or default_city
        try:
            origin = _resolve_endpoint(
                client, raw.get('origin'), raw.get('name'),
                raw.get('origin_city') or city)
            destination = _resolve_endpoint(
                client, raw.get('destination'), raw.get('to_name'),
                raw.get('destination_city') or city)
            route = client.route(origin, destination, mode=mode, city=city)
        except (AmapError, ValueError) as exc:
            errors.append({'leg': label,
                           'error': '%s: %s' % (type(exc).__name__, exc)})
            continue
        row = to_dict(route)
        row.update({'from_id': raw.get('from_id'), 'to_id': raw.get('to_id')})
        legs_out.append(row)

    _emit({
        '_说明': ('本文件是**采集结果**，不是路书声明——进路书的数字须另写，'
                  '且不得超过此处实采范围。单段失败在 errors 里，不是静默跳过。'),
        'provider': 'amap',
        'mode': mode,
        'checked_at': datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%dT%H:%M%z'),
        'legs': legs_out,
        'errors': errors,
    }, args.output)
    _write_trace(client, args.trace)


def command_amap_snapshot(args: argparse.Namespace) -> None:
    request = _read_json(args.input)
    client = _amap_client()
    _emit(collect_amap_snapshot(request, client,
                                keep_raw=not args.no_keep_raw), args.output)


def command_weather(args: argparse.Namespace) -> None:
    """实况 + 预报 —— 但先说清「能查到哪一天」，再说那天怎么样。

    这条命令的定位是**临行前**，不是规划期：窗口只有 4 天，
    半个月后的行程现在必然查不到。所以它把「查不到哪几天」当成一等输出，
    而不是让调用方从一张空表里自己猜。路书里因此**不写未来天气**，
    只写「出发前 3 天再查一次」——写下的多半会变。
    """
    try:
        payload = _amap_client().weather(args.city, forecast=True)
    except AmapError as exc:
        _emit_line('❌ 天气查询失败：%s' % exc)
        if not str(args.city).strip().isdigit():
            _emit_line('   若是城市名，请写全称（「中山市」而不是「中山」）——'
                       '2026-09-27 实测：高德天气要求完整行政区名，')
            _emit_line('   简名「中山」带 all 会直接报 20003，带 base 则会静默返回'
                       '「大连市中山区」。')
            _emit_line('   最稳的是直接给 adcode，如 --city 442000（广东中山市）。')
        raise SystemExit(2)

    report = assess_forecast(payload, args.start, args.end)

    if args.json:
        _emit(report, args.output or None)
        return

    # 空返回必须报错，不能让它变成一张安静的空表。
    # 实测「Tokyo」：高德不报错、返回 status=1 但 lives/forecasts 全空 ——
    # 静默的空表读起来像「今天没数据」，实际是「这里根本不覆盖」。
    if not payload.get('forecast') and not payload.get('live'):
        _emit_line('❌ 高德没有返回「%s」的任何天气数据。' % args.city)
        _emit_line('   常见原因：位置在中国大陆之外（高德天气不覆盖境外），'
                   '或城市名 / adcode 写错。')
        raise SystemExit(2)

    # 覆盖门禁：高德对境外地点不报错，而是换个城市回答 —— 与 geocode 同一个坑
    if report.get('matched') is False:
        _emit_line('⚠️ 高德返回的是「%s」，与查询的「%s」不符。'
                   % (report.get('returned_city'), report.get('query')))
        _emit_line('   高德的天气数据以中国大陆为主；查境外地点它会返回别的城市。本结果不可用。')
        raise SystemExit(2)

    # 同名陷阱：高德返回的 adcode 末尾不是 00，说明落到区级而非城市。
    # 实测「中山」会拿到「大连市中山区」——名字对得上，地方差了 2000 公里。
    if report.get('settlement_level') is False and not str(args.city).strip().isdigit():
        _emit_line('⚠️ 高德把「%s」解析成了「%s」（adcode %s）—— 这是同级同名的区县，'
                   '不是你要去的城市。' % (args.city, report.get('returned_city'),
                                           report.get('adcode')))
        _emit_line('   换成 adcode 重查，例如：--city 442000（广东中山市）。')
        raise SystemExit(2)

    live = payload.get('live') or {}
    head = '%s（返回：%s）' % (payload.get('query'), report.get('returned_city') or '—')
    _emit_line('【天气】%s' % head)
    _emit_line('查询时刻 %s（高德 weatherInfo，[A] 工具实证）'
               % (report.get('report_time') or payload.get('checked_at')))
    if live:
        _emit_line('实况：%s %s℃ %s风 %s级 湿度 %s%%'
                   % (live.get('weather'), live.get('temperature'),
                      live.get('wind_direction'), live.get('wind_power'),
                      live.get('humidity')))
    _emit_line('')
    _emit_line(report['reach'])
    _emit_line('')

    covered = report.get('covered') or []
    if covered:
        _emit_line('  %-12s %-6s %-8s %-8s %-10s %s'
                   % ('日期', '星期', '白天', '夜间', '温度', '提示'))
        for c in covered:
            _emit_line('  %-12s %-6s %-8s %-8s %-10s %s'
                       % (c['date'], c.get('weekday') or '',
                          c.get('day_weather') or '', c.get('night_weather') or '',
                          '%s/%s℃' % (c.get('day_temp'), c.get('night_temp')),
                          c.get('note') or ''))
    if report.get('uncovered'):
        _emit_line('')
        _emit_line('  暂无预报：%s' % '、'.join(report['uncovered']))

    _emit_line('')
    _emit_line('本命令只报「预报说了什么」，不替行程做决定；雨天提示只是提醒留备选。')
    _emit_line('温度单位 ℃；数据来自高德开放平台，时戳如上，过期请重查。')


def command_evaluate(args: argparse.Namespace) -> None:
    now = _parse_datetime(args.now) if args.now else None
    report = evaluate_itinerary(_read_json(args.input), now=now)
    _emit(report, args.output)
    # 「排不通不给你」的闸门必须能被程序感知 —— 上游这里只打印就正常退出
    if str(report.get('status', '')).upper() == 'INFEASIBLE':
        raise SystemExit(2)


def command_compile_research(args: argparse.Namespace) -> None:
    _emit(compile_destination_brief(_read_json(args.input)), args.output)


def command_normalize_rail(args: argparse.Namespace) -> None:
    report = normalize_query_result(_read_json(args.input))
    if args.select:
        report['trains'] = select_trains(
            report['trains'],
            seat_class=args.seat_class,
            earliest_departure=args.earliest,
            latest_departure=args.latest,
            max_duration_minutes=args.max_duration,
            require_seat=not args.include_sold_out,
            limit=args.limit,
        )
        report['count'] = len(report['trains'])
    _emit(report, args.output)


def command_validate_flights(args: argparse.Namespace) -> None:
    payload = _read_json(args.input)
    offers = payload if isinstance(payload, list) else (payload.get('flight_offers') or [])
    now = None if args.skip_freshness else (
        _parse_datetime(args.now) if args.now else datetime.now(timezone.utc)
    )
    report = validate_flight_offers(offers, now=now, max_age_hours=args.max_age_hours)
    _emit(report, args.output)
    if report['status'] == 'INVALID':
        raise SystemExit(2)


def command_validate_lodging(args: argparse.Namespace) -> None:
    payload = _read_json(args.input)
    offers = payload if isinstance(payload, list) else (payload.get('lodging_offers') or [])
    now = None if args.skip_freshness else (
        _parse_datetime(args.now) if args.now else datetime.now(timezone.utc)
    )
    report = validate_lodging_offers(
        offers, now=now, max_age_hours=args.max_age_hours, rooms=args.rooms
    )
    _emit(report, args.output)
    if report['status'] == 'INVALID':
        raise SystemExit(2)


def command_validate_plan(args: argparse.Namespace) -> None:
    report = validate_plan_content(_read_json(args.input))
    _emit(report, args.output)
    if report['status'] == 'INVALID':
        raise SystemExit(2)
    if report['status'] == 'INCOMPLETE_EVIDENCE':
        # 与 INVALID 分开且仍非零：不完整的方案是个真实结果，
        # 但绝不能被误当成完整方案。
        raise SystemExit(3)


# ============================ 参数表 ============================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='travel_planner.py',
        description='可核验旅行规划器 · 统一命令行入口（除 amap-* 外均只读）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='command', required=True, metavar='<命令>')

    p = sub.add_parser('credential-status', help='看 key 配没配上、从哪读到的')
    p.set_defaults(func=command_credential_status)

    p = sub.add_parser('preflight', help='实测 key 能否真连高德（会联网）')
    p.set_defaults(func=command_preflight)

    p = sub.add_parser('doctor', help='环境体检（本机能力 + 高德 + 铁路 + 浏览器）')
    p.add_argument('--live', action='store_true',
                   help='key 已配时发一次真实高德请求')
    p.add_argument('--client',
                   choices=('auto', 'workbuddy', 'zcode', 'codex', 'claude-code', 'generic'),
                   default='auto',
                   help='检测哪个客户端里的 MCP 注册（zcode 读工作区 .mcp.json，'
                        'workbuddy 读 ~/.workbuddy/mcp.json）')
    p.add_argument('--browser-status', choices=('available', 'unavailable', 'unknown'),
                   default='unknown', dest='browser_status',
                   help='由调用方 Agent 上报浏览器能力')
    p.set_defaults(func=command_doctor)

    p = sub.add_parser('validate-request', help='需求采集校验（缺项 -> 追问）')
    p.add_argument('--input', required=True)
    p.add_argument('--output')
    p.set_defaults(func=command_validate_request)

    p = sub.add_parser('search-places', help='查高德 POI（需 key）')
    p.add_argument('--keywords', required=True)
    p.add_argument('--city')
    p.add_argument('--limit', type=int, default=10)
    p.add_argument('--trace',
                   help='把本次全部成功调用的原始返回体写到该文件（来源留痕）')
    p.set_defaults(func=command_search_places)

    p = sub.add_parser('nearby-spots',
                       help='沿主路线各站采「顺道可去」的候选点（需 key；只列不判断）')
    p.add_argument('--input', required=True,
                   help='{"city":"上海","radius_meters":1000,"limit":8,'
                        '"stops":[{"name":"外滩"},{"name":"豫园"}]}')
    p.add_argument('--output')
    p.add_argument('--trace',
                   help='把本次全部成功调用的原始返回体写到该文件（来源留痕）')
    p.set_defaults(func=command_nearby_spots)

    p = sub.add_parser('route',
                       help='点对点路线：距离 / 时长 / 过路费·票价（需 key；逐段留痕）')
    p.add_argument('--input', required=True,
                   help='{"mode":"driving","city":"东莞","legs":[{"from_id":"a",'
                        '"to_id":"b","origin":"113.66,22.82",'
                        '"destination":"113.75,22.90"}]}')
    p.add_argument('--output')
    p.add_argument('--trace',
                   help='把本次全部成功调用的原始返回体写到该文件（来源留痕）')
    p.set_defaults(func=command_route)

    p = sub.add_parser('amap-snapshot', help='一次采集坐标 + 路线 + 周边（需 key）')
    p.add_argument('--input', required=True)
    p.add_argument('--output')
    p.add_argument('--no-keep-raw', action='store_true',
                   help='不在快照内嵌原始返回体（默认内嵌 raw_calls，来源留痕）')
    p.set_defaults(func=command_amap_snapshot)

    p = sub.add_parser('weather', help='实况 + 预报（需 key；预报窗口约 4 天，属临行前能力）')
    p.add_argument('--city', required=True,
                   help='adcode（推荐，如 442000）或完整行政区名（如 中山市）')
    p.add_argument('--start', help='行程首日 YYYY-MM-DD（用于判定预报够不够远）')
    p.add_argument('--end', help='行程末日 YYYY-MM-DD')
    p.add_argument('--json', action='store_true', help='输出机器可读 JSON')
    p.add_argument('--output', help='配合 --json 写文件')
    p.set_defaults(func=command_weather)

    p = sub.add_parser('evaluate', help='确定性可行性检查（排不通不给你）')
    p.add_argument('--input', required=True)
    p.add_argument('--output')
    p.add_argument('--now', help='检查基准时刻（ISO8601 带时区），缺省=本机当前时间')
    p.set_defaults(func=command_evaluate)

    p = sub.add_parser('compile-research', help='社区线索汇编成景点卡')
    p.add_argument('--input', required=True)
    p.add_argument('--output')
    p.set_defaults(func=command_compile_research)

    p = sub.add_parser('normalize-rail',
                       help='12306 余票归一化（「有/无/数字」混合值，禁直接 int()）')
    p.add_argument('--input', required=True)
    p.add_argument('--output')
    p.add_argument('--select', action='store_true', help='顺带筛出可用候选')
    p.add_argument('--seat-class', dest='seat_class')
    p.add_argument('--earliest', help='最早发车 HH:MM')
    p.add_argument('--latest', help='最晚发车 HH:MM')
    p.add_argument('--max-duration', type=int, dest='max_duration', help='最长耗时（分钟）')
    p.add_argument('--limit', type=int, default=10)
    p.add_argument('--include-sold-out', action='store_true', dest='include_sold_out')
    p.set_defaults(func=command_normalize_rail)

    p = sub.add_parser('validate-flights', help='机票报价校验（含价格新鲜度）')
    p.add_argument('--input', required=True)
    p.add_argument('--output')
    p.add_argument('--now', help='ISO8601 带时区，用于比对新鲜度（缺省=现在）')
    p.add_argument('--max-age-hours', type=int, default=FLIGHT_MAX_AGE_HOURS,
                   dest='max_age_hours', help='超过此小时数须重查（缺省 2）')
    p.add_argument('--skip-freshness', action='store_true', dest='skip_freshness',
                   help='只做结构检查，不比时钟')
    p.set_defaults(func=command_validate_flights)

    p = sub.add_parser('validate-lodging', help='酒店报价校验（推导住宿总价）')
    p.add_argument('--input', required=True)
    p.add_argument('--output')
    p.add_argument('--rooms', type=int, default=1)
    p.add_argument('--max-age-hours', type=int, default=LODGING_MAX_AGE_HOURS,
                   dest='max_age_hours', help='超过此小时数须重查（缺省 12）')
    p.add_argument('--now')
    p.add_argument('--skip-freshness', action='store_true', dest='skip_freshness')
    p.set_defaults(func=command_validate_lodging)

    p = sub.add_parser('validate-plan', help='方案内容完整性（内连带跑机票/酒店校验）')
    p.add_argument('--input', required=True)
    p.add_argument('--output')
    p.set_defaults(func=command_validate_plan)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except (CredentialError, AmapError, OSError, ValueError,
            json.JSONDecodeError) as exc:
        _emit({'status': 'ERROR',
               'error_type': exc.__class__.__name__,
               'message': str(exc)})
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
