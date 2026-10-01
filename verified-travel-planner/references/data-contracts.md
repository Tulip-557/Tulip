# 数据契约

路书管线的每一层产物都用 JSON 表达，这样它能被**机器校验**，而不只是被人读。
本文件定义各层的字段与硬性规则。字段名与检查码保留英文（代码里就是这些字面量），
说明用中文。

> **本文件的字段以 `engine/travel_planner/` 的实际实现为准。**
> 若发现文档与代码不符，以代码为准并回来改这里——历史上本文件曾两次写错字段名
> （`date_start` 写成 `days[]`），都是"文档说有、引擎不认"的坑。

**分层**

| 层 | 产物 | 谁产出 |
|---|---|---|
| 需求层 | `trip_request.json` | 需求采集（阶段 0） |
| 来源层 | source metadata / connector status / unavailable sources | 各采集环节 |
| 采集层 | Amap 坐标与路线 / Rail Option / Flight Offer / Social Research | 地图 / 铁路 / 机票 / 社区 |
| 汇编层 | `destination_brief.json` | `compile-research` |
| 方案层 | `final_plan.json` / `itinerary.json` / `feasibility_report.json` | 规划与校验 |
| 交付层 | 路书事实源 JSON → HTML | `render_html.py` |

---

# 一、需求层 · trip_request.json

**必须先跑 `validate-request` 再开始调研。**

```json
{
  "origin": "广州",
  "destination": "桂林与阳朔",
  "origin_city": "广州",
  "destination_city": "桂林",
  "start_date": "2027-04-10",
  "end_date": "2027-04-13",
  "travelers": 2,
  "budget_cny": 3500,
  "budget_scope": "PER_PERSON",
  "style": "balanced",
  "must_visit": [{"name": "龙脊梯田", "priority": "CORE"}],
  "planned_places": [{"name": "东方明珠", "status": "BOOKED", "note": "已订 9/29 下午场"}],
  "excluded_places": [],
  "mobility": {
    "level": "MODERATE",
    "max_walking_km_per_day": 8,
    "accepts_high_altitude": true,
    "accessibility_needs": []
  },
  "tradeoff_priority": ["CORE_PLACES", "COST", "PACE", "COMFORT"],
  "risk_tolerance": {"accepts_weather_dependent_core": true},
  "preferences": {
    "diet": "不吃辣、想吃本帮菜",
    "pace": {"wake_time": "9 点前不起床", "nap": "午饭后要小睡"},
    "interests": ["建筑", "本地菜市场", "避开人挤人的景点"]
  },
  "browser_approval": {"xiaohongshu": "ALLOW_MANUAL_LOGIN", "ota": "ANONYMOUS_ONLY"},
  "route_modes": ["transit", "driving"],
  "transport_preferences": {
    "accepts_early_departure": true,
    "accepts_overnight_transport": false,
    "accepts_transfers": true
  },
  "latest_return_time": null,
  "discovery": {"radius_meters": 10000, "types": "110000|140000", "limit": 15}
}
```

### 必填（缺一项就阻塞，共 10 个）

`origin` `destination` `start_date` `end_date` `travelers` `budget_cny`
`budget_scope` `style` `mobility` `browser_approval`

其中**只有四项没有安全默认值**，因为每一项都会实质改变方案：

- `budget_scope` —— 人均 2000 和全团 2000 差一倍
- `mobility.level` —— 直接决定哪些行程可行
- `browser_approval` —— 这是**同意**，不能替人做主
- `style` —— 深度体验与特种兵暴走排出的日行程槽数完全不同

### 可假设（缺了不阻塞，但必须写进 `assumptions`）

| 字段 | 缺省 | 依据 |
|---|---|---|
| `must_visit` | `[]` | 没有地点豁免于取舍 |
| `planned_places` | `[]` | 用户没提已经定好要去哪 |
| `excluded_places` | `[]` | 没有要避开的 |
| `tradeoff_priority` | `CORE_PLACES, COST, PACE, COMFORT` | 先保核心景点，再省钱 |
| `risk_tolerance` | 接受看天景点 | 若变成决定性因素会再问 |
| `preferences` | `{}` | 用户没报饮食/作息/兴趣，主线不做针对性调整 |
| `mobility.max_walking_km_per_day` | 按等级 4 / 8 / 15 | 说了等级就已经答了 |
| `mobility.accepts_high_altitude` | 接受 | 出现高海拔核心点时会再问 |
| `mobility.accessibility_needs` | 无 | — |

> 问这些会把一句人话变成一份表格——没人会主动说"我没有要避开的地方"。
> **但给了值就照常校验**：缺省不放松对已有值的检查。

### `planned_places` 与 `must_visit` 的分工（别混）

| | `must_visit` | `planned_places` |
|---|---|---|
| 回答什么 | 哪些地方**不能砍** | 用户**已经打算**去哪几个点 |
| 性质 | 取舍约束 | 行程锚点（用户心里已经有它） |
| 强度 | `CORE` 不可移除 | `BOOKED` 固定时间窗 ｜ `INTENDED` 可调 |
| 缺省 | `[]` | `[]` |

- `BOOKED`（已订票 / 已预约）→ 时间窗固定，**必须进 `itinerary_*.json`**，
  让可行性引擎校验「排得进去吗」；路书里要写明几点入场、路上预留多少。
- `INTENDED`（只是想去）→ 排程**优先串进去**；若排不进或与天气冲突，
  要在路书里**说明为什么没纳入**，不能默默丢掉。
- 任一项同时出现在 `excluded_places` 里 → 报 `conflicts`（既要去又要避开，说不通）。

### `preferences` 的边界（用户自报，非推断）

`preferences` 只承载**用户自己说出口的**饮食忌口/喜好、作息休息、兴趣——
用于让主路线更贴合本人（如「不吃辣」就把川菜档换成别的、「9 点前不起床」就不排早班）。
三个键都是软字段：

| 键 | 含义 | 形态 |
|---|---|---|
| `diet` | 饮食忌口 / 喜好 | 字符串或字符串数组 |
| `pace` | 作息与休息 | 字符串或 `{wake_time, nap}` 对象 |
| `interests` | 兴趣标签 | 字符串或字符串数组 |

> ⚠️ **绝不在这里放模型推断值**——推断会冒充采集值，违背「每个数字都有来源」。
> 用户没说就是 `{}`（进 `assumptions`），追问不猜。缺哪键删哪键，三键全空可整个删掉。

### 枚举值

```text
budget_scope:        PER_PERSON | PARTY_TOTAL
must_visit[].priority: CORE | IMPORTANT | OPTIONAL
planned_places[].status: BOOKED | INTENDED
mobility.level:      LOW | MODERATE | HIGH
tradeoff_priority[]: CORE_PLACES | COST | PACE | COMFORT
browser_approval.*:  ANONYMOUS_ONLY | ALLOW_MANUAL_LOGIN | DENY
```

`CORE` 地点不可移除；方案修复可依 `tradeoff_priority` 改动可选地点、费用、节奏、舒适度。
**安全与合法通行始终是绝对约束**，不参与取舍。

### 三种状态

| status | 含义 | 该做什么 |
|---|---|---|
| `READY` | 齐了，且无冲突 | **不要再问任何问题**，直接开工 |
| `NEEDS_CLARIFICATION` | 有缺项或冲突 | 一次性把所有缺项问完，别挤牙膏 |
| `INVALID` | 有错值 | 先让用户改，再调研 |

返回结构：

```json
{
  "status": "READY",
  "missing_fields": [],
  "errors": [],
  "conflicts": [],
  "assumptions": ["未指定必去地点，全部地点均可为省钱或节奏让路"],
  "questions_required": []
}
```

`conflicts` 的典型例子：同一个地点同时出现在 `must_visit` 和 `excluded_places`。

---

# 二、来源层

## Source Metadata

**每一个外部结果都要挂这个对象**——它是"这条信息哪来的、什么时候查的、能不能信"的载体。

```json
{
  "provider": "amap",
  "connector": "amap-web-api",
  "connector_type": "official_api",
  "channel": "api",
  "login_state": "NOT_APPLICABLE",
  "checked_at": "2026-08-10T10:00:00Z",
  "url": null,
  "confidence": "HIGH"
}
```

`connector_type` 枚举：

```text
official_api          官方 API（如高德）
community_mcp         社区 MCP（如 12306）
browser               浏览器读取（如携程、小红书）
web_search_fallback   网页搜索兜底
user_provided         用户直接提供
```

## Connector Status

```json
{
  "provider": "xiaohongshu",
  "status": "LOGIN_REQUIRED",
  "checked_at": "2026-08-10T10:00:00Z",
  "message": "搜索前需要人工登录"
}
```

状态枚举：

```text
READY / PUBLIC_READY / LOGIN_REQUIRED / CONNECTED / EXPIRED / DEGRADED / BLOCKED / UNAVAILABLE
```

## Unavailable Sources

方案可以声明哪些来源没打通——**这样缺失能报 `INCOMPLETE_EVIDENCE`，而不是直接判失败**：

```json
{
  "unavailable_sources": [
    {"provider": "amap", "reason": "目的地在高德覆盖范围外"},
    {"provider": "xiaohongshu", "reason": "匿名搜索为空，用户未授权登录"},
    {"provider": "12306", "reason": "境外目的地，铁路连接器不适用"}
  ]
}
```

被豁免的项会列在 `unmet_by_blocked_sources` 里。**其余错误照判**：
路线缺失或时间戳格式错，无论来源出没出问题，都是方案自己的错。

> **在方案里存在但内容是空的地点，永远不豁免。**
> 什么都没查到，那这个地方就不该出现在方案里。

---

# 三、采集层

## Amap Place and Route

`search-places` 与 `amap-snapshot` 的输出：

```json
{
  "locations": {
    "destination": {
      "name": "西湖",
      "longitude": 120.14,
      "latitude": 30.24,
      "city": "杭州市",
      "match": {
        "confidence": "HIGH",
        "name_in_address": true,
        "level": "风景名胜",
        "candidate_count": 1,
        "matched_address": "浙江省杭州市西湖",
        "reasons": []
      }
    }
  },
  "routes": [
    {
      "mode": "transit",
      "origin": {"...": "同上的 Location"},
      "destination": {"...": "同上的 Location"},
      "duration_minutes": 28,
      "distance_meters": 6200,
      "transfer_count": 1,
      "walking_distance_meters": 450,
      "estimated_cost": null,
      "source": null,
      "metadata": {}
    }
  ],
  "nearby_places": [
    {
      "name": "灵隐寺",
      "location": {"...": "同上的 Location"},
      "address": "杭州市西湖区法云弄1号",
      "category": "风景名胜",
      "rating": 4.6,
      "source": {
        "provider": "amap",
        "checked_at": "2026-08-10T10:00:00+08:00",
        "provider_id": null,
        "url": null
      }
    }
  ]
}
```

### `match` 字段的两种含义（容易误读）

`match` **只在走过 `geocode()` 的 Location 上存在**；若 `resolve_location()` 直接接受了
一个具名 POI，它是 `null`——景点本身可能很小，那条分支不做"行政区级别"的检查。

所以 **`match: null` 不等于匹配差**，更常见的意思是"这是个场所而非城市"。

### 为什么 `origin` / `destination` 必须走 `expect_settlement=True`

行程的两端是**行政区**，不是场所。用默认的 `resolve_location("东京")` 曾静默匹配到
北京一家同名餐厅——因为 POI 优先分支**没有任何检查会失败**，它绕过了本该拦下它的那道闸。
`expect_settlement=True` 会跳过该分支、直接走 `geocode(expect_settlement=True)`，
后者对 `LOW` 置信度的匹配**直接拒绝**而不是返回一个带 `confidence: "LOW"` 的结果。

`amap-snapshot` 对两端都这样调用，因此经它进入方案的 Location 至少是 `MEDIUM`。

### `Route.source` 与 `Route.estimated_cost` 常为 `null`

高德的公交与驾车方向接口**不总是带票价**，本模块**从不自己编一个填上**。

### `route` 命令（P1 来源留痕 · 点对点采集）

`amap-snapshot` 采的是**城市级**起终点（带覆盖门禁，防「查东京得广西村庄」）；
`route` 采的是**POI 级**点对点，与 `search-places` 同一档，不做行政级覆盖检查。
**两条通道别混用**：城市级查询必须走 `amap-snapshot` 的 `expect_settlement` 覆盖检查，
点位级走本命令——混用会同时破坏两边。

为什么要有它：`claim_audit` 的 C2 要把「9.5 公里 / 过路费 ¥17」这类声明回查留痕池，
但**此前没有任何命令能产出方向类留痕**（快照只覆盖城市级起终点，另两个 `--trace`
命令都不调方向接口）。没有本命令，城内的距离/票价就永远只能挂 UNVERIFIED。

输入：

```json
{
  "mode": "driving",
  "city": "东莞",
  "legs": [
    {"from_id": "a", "to_id": "b", "name": "虎门站",
     "origin": "113.66,22.82", "destination": "113.75,22.90",
     "origin_city": "东莞", "destination_city": "东莞"}
  ]
}
```

| 字段 | 规则 |
|---|---|
| `mode` | `driving` / `walking` / `transit` 三者之一。**非法值直接拒收**，不静默按驾车跑 |
| `origin` / `destination` | **坐标优先**（`"经度,纬度"`）——不吃关键字搜索配额，端点也确定；给名字才回退一次 `search-places` |
| `city` | 行程级默认城市，可被每段的 `city` 覆盖；`transit` 必需（高德要 `city`/`cityd`） |
| `origin_city` / `destination_city` | 跨城公交用；缺省继承 `city` |
| `from_id` / `to_id` | 与 `itinerary.segments` 的 id 对齐，供留痕比对关联 |

输出：逐段一份 `Route`（字段同上），另带 `from_id` / `to_id`；`errors[]` 记失败的段。
**单段失败只进 `errors`，不拖垮整批**——批量采 14 段时，一条坏路不该把另外 13 段的
实采值一起丢掉。

`--trace` 落盘的是本次全部**成功**调用的原始返回体（`{provider, generated_at,
call_count, calls[]}`，key 已脱敏）。`claim_audit` 的 `--snapshot` 按**形状**识别它——
方向接口的 `duration`（秒）/`distance`（米）/`tolls`·`cost` 会被换算成分钟/公里/票价
进比对池。**没有留痕的采集值不算证据**。

## Rail Option

方案消费的车次形态：

```json
{
  "mode": "rail",
  "train_code": "G2",
  "origin_station": "广州南",
  "destination_station": "桂林北",
  "departure": "2027-04-10T07:00:00+08:00",
  "arrival": "2027-04-10T09:45:00+08:00",
  "duration_minutes": 165,
  "seats": {"second_class": "有", "first_class": "3"},
  "prices_cny": {"second_class": 164, "first_class": 263},
  "source": {
    "provider": "12306",
    "connector": "drfccv/mcp-server-12306",
    "connector_type": "community_mcp",
    "official_connector": false,
    "checked_at": "2027-04-01T10:00:00+08:00"
  }
}
```

### 12306 原始数据（`query-tickets` 的返回）

```json
{
  "success": true,
  "from_station": "上海",
  "to_station": "杭州",
  "train_date": "2026-08-20",
  "trains": [
    {
      "train_no": "G1321",
      "from_station": "上海虹桥",
      "to_station": "杭州东",
      "start_time": "06:07",
      "arrive_time": "06:56",
      "duration": "00:49",
      "seats": {"business": "9", "first_class": "有", "second_class": "有", "no_seat": "无"}
    }
  ]
}
```

**四条硬规则**：

1. **余票不是数字。** 同一个字段里混着整数和汉字——剩余 20 张以内给准确数，
   充足时给 `有`，售罄给 `无`。**永远不要直接 `int()`**。必须先过
   `travel_planner.py normalize-rail`，它把每个值转成带 `status` / `count` / `at_least`
   的记录，这样候选之间才可比。
2. **`query-tickets` 不带票价。** 价格必须另调 `query-ticket-price`。
   **方案里不得出现一次都没查过的票价。**
3. **车次是 activity，不是 segment。** 火车时间本身就是"乘坐"，去车站那段才是交通段。
4. **不要按站名过滤。** 城市查询会返回该城所有同城站，邻站常比旅客点名的那个更快。

**查询节奏**：每秒最多 1 次 12306 请求；相同站点/日期结果缓存 1–5 分钟；
网络失败最多退避重试 2 次；**遇到验证码、封禁或连续非 JSON 响应就停**。

## Flight Offer

```json
{
  "mode": "flight",
  "offer_id": "ctrip-web-result-1",
  "outbound": {
    "carrier": "Example Air",
    "flight_number": "EX123",
    "origin_airport": "CAN",
    "destination_airport": "KWL",
    "departure": "2027-04-10T07:50:00+08:00",
    "arrival": "2027-04-10T09:10:00+08:00",
    "duration_minutes": 80,
    "stops": 0
  },
  "return": null,
  "displayed_total_price": 620,
  "currency": "CNY",
  "baggage_visibility": "UNKNOWN",
  "final_price_guaranteed": false,
  "source": {
    "provider": "ctrip",
    "connector": "browser-adapter",
    "connector_type": "browser",
    "channel": "ctrip_web",
    "login_state": "PUBLIC_READY",
    "page_visible_only": true,
    "checked_at": "2027-04-01T18:00:00+08:00",
    "url": "https://..."
  }
}
```

## Fare Calendar

机票页常有一排邻近日期的指示价。它能回答行程本身答不了的问题——**晚一天走是不是便宜一半**，
所以见到就采：

```json
{
  "route": "MMK-SHA",
  "currency": "CNY",
  "entries": [
    {"date": "2026-10-07", "indicative_price": 7133},
    {"date": "2026-10-08", "indicative_price": 3817}
  ],
  "source": {"provider": "ctrip", "connector_type": "browser", "channel": "ctrip_web",
             "checked_at": "2026-08-18T19:38:00+08:00"}
}
```

**这里的每个数字都是 `PRICE_SIGNAL`，从不是票价。** 那条价带宣传的低价可能对应
没人会接受的转机路线，也可能已经不存在。把它当作"值得去查那一天"的理由，
并标注未核实；**方案不得用它来算钱**。

## Social Research Result

浏览器结果**必须先归一化**，才能影响方案：

```json
{
  "title": "杭州三日游路线",
  "url": "https://www.xiaohongshu.com/...",
  "author_display_name": "可见的作者名",
  "visible_body": "可见正文",
  "published_at": null,
  "checked_at": "2026-08-10T10:00:00Z",
  "extracted_place_names": ["西湖", "灵隐寺"],
  "claims": [
    {"type": "QUEUE", "text": "8 点前到可避开排队", "confidence": "MEDIUM"}
  ],
  "place_evidence": [
    {
      "name": "西湖",
      "features": ["城市湖泊景观", "白堤与断桥步行线"],
      "why_visit": ["适合低强度慢游和日落散步"],
      "suggested_duration_minutes": 180,
      "best_time": "清晨或傍晚",
      "physical_load": "低至中等",
      "caveats": ["节假日断桥区域拥挤"]
    }
  ],
  "source": {
    "provider": "xiaohongshu",
    "connector": "browser-adapter",
    "connector_type": "browser",
    "channel": "xiaohongshu_web",
    "login_state": "CONNECTED",
    "page_visible_only": true
  }
}
```

### `claims[].type` —— 证据类别决定它能走多远

```text
ROUTE_HYPOTHESIS   路线假设：只能给一天排个序，最终由高德计算 + 可行性检查裁定
TRAVEL_TIME_HINT   时长线索：高德会重算，有分歧以高德为准
PRICE_SIGNAL       价格信号：可用于预算讨论，但必须标未核实，绝不自动变 estimated_cost
EXPERIENCE         体验建议：可原样采用，注明出处
SEASONAL           季节性：可用于时机建议，注明出处；行程贴近边界时标注
QUEUE / CLOSURE    排队 / 关闭：可原样采用，注明出处
```

**营业时间、门票价、列车时刻、余票，永远不从社区笔记取。** 不论笔记说了什么，
这些一律回场馆、高德或 12306。

> 这不是在丢社区知识——**恰恰是它让社区知识全部都能用上**。
> 一条记为"假设"的说法可以塑造路线，同时从不会作为事实呈现给旅客。

### 图片提取

从笔记图片里读出的说法，除证据类别外还要带 `"extraction": "image"` 与图片序号。

小红书是图片优先的平台——**只读了文字的笔记等于没读过**。逐日行程图和费用明细表
通常画在轮播图里，正文只有一段引言。所以图片提取是**常态而非例外**。

但**从表格里读出来的数字，仍然是一个旅客一次行程的一张收据**，依旧是 `PRICE_SIGNAL`。

### 复用与合并（`social_notes.py --merge`）

线索卡是**跨行程资产**，不是一次性检索产物。合并语义写死如下，别自行发挥：

| 情形 | 处理 | 字段 |
|---|---|---|
| 同 `url`（无 url 用标题）本次重采到 | 用**新**条目覆盖，旧的丢弃 | 计入 `duplicates_dropped` |
| 旧卡有、本次没采到 | **保留** | `carried_over: true` |
| 沿用且超过 `--stale-days`（默认 90）或从无 `checked_at` | 保留但点名 | 再加 `stale: true` |

合并后的整卡**再走一遍死线校验**——「上次写对了所以这次不查」是这套规则最不能接受的心态。

输出附 `coverage` 块（不进任何评分，只回答「这次到底采到了什么」）：

```json
{"notes": 7, "by_level": {"B": 4, "C": 3}, "connector_types": ["web_search_fallback"],
 "errors": 0, "warnings": 0,
 "new": 2, "carried_over": 6, "stale": 6, "duplicates_dropped": 1, "stale_days": 90}
```

### 交付声明 · `meta.social_intel`

阶段 3.5 是**默认动作**，所以交付物里必须留痕。声明写在事实源的 `meta.social_intel`，
渲染器把它变成 `#overview` 顶部的「情报来源」条，交付自查第 ㉗ 项据此判定：

```json
{"status": "COLLECTED", "paths": ["官方文旅发布", "搜索引擎索引"],
 "notes_file": "社媒线索_中山.json", "note_count": 8,
 "checked_at": "2026-09-27", "skipped_reason": null}
```

`status` 二选一：`COLLECTED`（须给 `paths`/`note_count`）或 `SKIPPED`（须给 `skipped_reason`）。
**两种出口都算过，静默跳过不算**——判据是留痕，不是必须采到。

---

# 四、汇编层 · destination_brief.json

`compile-research` 的输出：

```json
{
  "status": "VALID",
  "destination": "杭州",
  "travel_style": "relaxed",
  "attraction_cards": [
    {
      "name": "西湖",
      "features": ["城市湖泊景观", "白堤与断桥步行线"],
      "why_visit": ["适合低强度慢游和日落散步"],
      "suggested_duration_minutes": 180,
      "best_time": ["清晨或傍晚"],
      "physical_load": ["低至中等"],
      "caveats": ["节假日断桥区域拥挤"],
      "source_refs": [
        {"title": "杭州松弛旅行", "url": "https://www.xiaohongshu.com/...",
         "published_at": "2026-06-01", "checked_at": "2026-08-10T10:00:00+08:00"}
      ],
      "evidence_count": 1,
      "missing_fields": []
    }
  ],
  "errors": [],
  "warnings": []
}
```

**缺少特色、理由、时长或来源的卡片是不完整的**，必须在生成路线前补齐。

> `place_evidence` 对影响路线的笔记是**必填**的。
> 不要让 `compile-research` 从任意散文里推断结构化事实。

## Lodging Offer

```json
{
  "offer_id": "ctrip-hotel-1",
  "name": "布尔津某酒店",
  "city": "布尔津",
  "room_type": "标准大床房",
  "check_in": "2026-10-04",
  "check_out": "2026-10-10",
  "displayed_price": 556,
  "price_basis": "PER_NIGHT",
  "free_cancellation": true,
  "rating": 4.7,
  "source": {
    "provider": "ctrip",
    "connector_type": "browser",
    "channel": "ctrip_hotel_web",
    "login_state": "CONNECTED",
    "member_tier": "钻石贵宾",
    "checked_at": "2026-08-18T20:00:00+08:00"
  }
}
```

### 酒店报价的两条隐含前提

**① `price_basis` 决定总价怎么算。**
只能是 `PER_NIGHT` 或 `TOTAL_STAY`。**永远不要把住宿总价写进 `displayed_price`**——
总价一律从晚数与间数推导，因为**把卡面价当总价会把一周少算六倍**：
六晚的 `¥556 起` 是 `¥3,336`，不是 `¥556`。

**② `login_state` 与 `member_tier` 是必填项，不是描述性字段。**
OTA 对未登录访客**完全不显示房价**，对已登录访客显示按等级缩放的价格——
同一间房公开 `¥609`、钻石会员 `¥479`。
**缺这两个字段的报价无法与任何其它报价比较**；混用不同登录态或等级采集的报价
会报 `MIXED_VIEWING_CONTEXTS`，而不是排出一个没有意义的名次。

> 另外：城市选择走**数字 id**，不是城市名。关键词参数会被静默忽略并返回另一个城市的酒店，
> 所以信任何一个价格之前，先在页面上确认城市。

---

# 五、方案层

## final_plan.json（`validate-plan` 的输入）

```json
{
  "name": "balanced",
  "days": [
    {
      "date": "2026-10-01",
      "theme": "西湖慢游",
      "activities": [
        {
          "id": "west-lake",
          "type": "ATTRACTION",
          "name": "西湖",
          "description": "沿白堤和湖滨慢走，观察湖光与城市边界。",
          "features": ["城市湖泊景观", "白堤与断桥步行线"],
          "why_visit": ["适合低强度慢游和日落散步"],
          "suggested_duration_minutes": 180,
          "best_time": "傍晚",
          "physical_load": "低至中等",
          "caveats": ["断桥区域可能拥挤"],
          "source_refs": ["xhs-note-1"]
        }
      ]
    }
  ],
  "segments": [],
  "flight_offers": [],
  "lodging_offers": [],
  "sources": [
    {"id": "xhs-note-1", "url": "https://www.xiaohongshu.com/...",
     "checked_at": "2026-08-10T10:00:00+08:00"}
  ]
}
```

**每个主要景点都要说明它的特色与入选理由。只含交通活动的方案无效。**

> **`validate-plan` 会内部连带跑 `validate-flights` 与 `validate-lodging`**
> （只要方案里存在对应数组）——所以一份坏掉的报价会让整个方案失败，
> **即使方案里没有别的地方引用它**。

## itinerary.json（`evaluate` 的输入）

**注意：这不是最终方案，是给可行性引擎的归一化输入。** 结构与 `final_plan.json` 不同。

```json
{
  "budget_cny": 1000,
  "timezone": "Asia/Shanghai",
  "constraints": {
    "max_daily_minutes": 720,
    "max_walking_km_per_day": 10,
    "default_transfer_buffer_minutes": 20,
    "stale_after_hours": 24
  },
  "activities": [
    {
      "id": "place-1",
      "name": "西湖",
      "type": "ATTRACTION",
      "start": "2026-10-01T09:00:00+08:00",
      "end": "2026-10-01T11:30:00+08:00",
      "opening_time": "00:00",
      "closing_time": "23:59",
      "last_entry_time": null,
      "estimated_cost": 0,
      "walking_km": 4,
      "source_checked_at": "2026-10-01T00:00:00+08:00"
    }
  ],
  "segments": [
    {
      "from_id": "place-1",
      "to_id": "place-2",
      "duration_minutes": 40,
      "buffer_minutes": 20,
      "estimated_cost": 6
    }
  ]
}
```

### 字段规则（硬性）

| 字段 | 规则 |
|---|---|
| `start` / `end` | **必须带时区偏移**（`+08:00`）。裸时间会被拒——猜时区正是要防的错 |
| `timezone` | IANA 名（`Asia/Shanghai`）。Windows 上需装 `tzdata`，否则回退到时间戳偏移并警告 |
| `opening_time` / `closing_time` / `last_entry_time` | `HH:MM`。用于校验是否撞闭馆 / 停止入场 |
| `estimated_cost` | **纯数字**，不带 `¥` 与千分位逗号——带任何一项都会报 `UNREADABLE_NUMBER` |
| `duration_minutes` | 交通段实际耗时，**必须来自地图调用**，不是估的 |
| `source_checked_at` | 动态数据必填，用于时效判定 |

### 时区

`opening_time` 等是**场馆墙钟时间**，所以引擎要知道按哪个表读 `start` / `end`。

- `timezone`（行程级）是 IANA 名，**只要提供了营业时间就必须设**
- 活动可自带 `timezone` 覆盖行程级——跨国行程用它
- 未声明时区时，读 `start` / `end` 自带的偏移当作场馆本地钟。
  **仅当产出方写的就是目的地本地时间时才正确**
- 未声明时区**且**时间戳是 UTC 时，营业时间检查被跳过并报 `AMBIGUOUS_TIMEZONE`——
  拿 UTC 钟去比本地营业时间没有意义

> 优先用 IANA 名而不是固定偏移：前者带夏令时规则，后者表达不了。

### 跨天换乘

相邻活动落在不同本地日期时，之间隔着一夜，**不报缺段**。
但**跨天且明确声明了的段**（如过夜火车）仍然检查换乘时间是否够。

### 换乘缓冲的取值顺序

显式写的 `0` 会被尊重，不会被后面的默认值覆盖：

```text
段的 buffer_minutes
  → 下一个活动的 required_buffer_minutes
  → 该 type 的出发默认值（国内航班 120 / 国际航班 180 / 火车 45 / 大巴 30）
  → constraints.default_transfer_buffer_minutes
  → 15
```

### 分数分档

`score` 按 `status` 分档，**保证"被拦下的方案"永远不可能排在"仅是有风险"的前面**：

```text
FEASIBLE            = 100
FEASIBLE_WITH_RISK  = 60–95
INFEASIBLE          ≤ 40
```

**分数只在同一 status 内比较。**

### 判定规则

出现任一 HARD 问题 → `INFEASIBLE`；只有 WARNING → `FEASIBLE_WITH_RISK`；否则 `FEASIBLE`。

**修复顺序（最多 3 轮）**：
1. 调活动时间 → 2. 加换乘缓冲 → 3. 换交通方式 → 4. 调相邻顺序 → 5. 移到别的天 → 6. 砍最低优先级的可选点

## feasibility_report.json

```json
{
  "status": "PASS",
  "score": 100,
  "checks": [{"name": "时间窗", "result": "PASS"}],
  "hard_conflicts": [],
  "warnings": [],
  "skipped_checks": [{"name": "地面路线", "reason": "境外目的地，无地图覆盖"}]
}
```

| status | 含义 | 怎么处理 |
|---|---|---|
| `FEASIBLE` / `PASS` | 全通过 | 可交付 |
| `FEASIBLE_WITH_RISK` / `WARN` | 有警告 | **交付时必须把警告一并写出** |
| `INCOMPLETE_EVIDENCE` | **关键数据缺失，检查没做全** | 必须在路书中说明哪些没做 |
| `INFEASIBLE` / `FAIL` | 有硬冲突 | **不得作为可执行方案交付**，先修再交 |

> `INCOMPLETE_EVIDENCE` 与 `FAIL` 是两回事：前者是"数据不够，判不了"，
> 后者是"判了，通不过"。**不许把前者伪装成后者，也不许把前者伪装成 PASS。**

---

# 六、检查码表

从 `engine/travel_planner/` 的实际实现抽取，**不是从文档抄的**。

## 可行性（`evaluate`）

| Code | 级别 | 含义 |
|---|---|---|
| `INVALID_ACTIVITY_TIME` | HARD | `start` / `end` 缺失、格式错或没带偏移 |
| `INVALID_ACTIVITY_RANGE` | HARD | `end` 不晚于 `start` |
| `DUPLICATE_ACTIVITY_ID` | HARD | 两个活动共用 `id`；段是按它索引的 |
| `ACTIVITY_OVERLAP` | HARD | 两个活动占用同一段时间 |
| `INSUFFICIENT_TRANSFER_TIME` | HARD | 交通 + 缓冲超过间隙 |
| `BEFORE_OPENING` | HARD | 到达早于 `opening_time` |
| `AFTER_CLOSING` | HARD | 离开晚于 `closing_time`，或跨过午夜 |
| `AFTER_LAST_ENTRY` | HARD | 到达晚于 `last_entry_time` |
| `UNREADABLE_NUMBER` | HARD | 数字字段解析不了——典型是价格被抄成 `"¥620"` |
| `NEGATIVE_VALUE` | HARD | 时长 / 缓冲 / 费用 / 距离为负 |
| `MISSING_TRANSIT_SEGMENT` | WARNING | 同日相邻活动之间没有段 |
| `AMBIGUOUS_TIMEZONE` | WARNING | UTC 时间戳且未声明时区；营业时间检查被跳过 |
| `UNKNOWN_TIMEZONE` | WARNING | 声明的 IANA 名解析不了 |
| `INVALID_TIME_FORMAT` | WARNING | `HH:MM` 窗口字段格式错，已跳过 |
| `STALE_SOURCE` | WARNING | `source_checked_at` 超过 `stale_after_hours` |
| `INVALID_SOURCE_TIME` | WARNING | `source_checked_at` 解析不了 |
| `DAILY_DURATION_EXCEEDED` | WARNING | 单日跨度超过 `max_daily_minutes` |
| `WALKING_LIMIT_EXCEEDED` | WARNING | 单日步行超过 `max_walking_km_per_day` |
| `BUDGET_EXCEEDED` | WARNING | 费用合计超过 `budget_cny` |
| `EMPTY_ITINERARY` | **HARD**（2026-09-28 由 WARNING 提级） | `activities` 为空——没有可检查的内容 |

> **`EMPTY_ITINERARY` 拦的是"假绿"**：`activities` 为空时所有规则都被跳过，函数会返回
> 一个干净的 `FEASIBLE 100`，而最常见的成因不是空行程，是**把只有 `days`/`segments`
> 的方案层 `final_plan_*.json` 喂给了只认行程的检查**。那种情况下 100 分的意思是
> 「什么都没查」，读起来却完全像「全都通过了」。2026-09-27 实际踩到过一次。
>
> **2026-09-28 提为阻断级**：作为 WARNING 时它只让结论变成 `FEASIBLE_WITH_RISK / 88`，
> **退出码仍是 0** —— 对任何自动化消费者来说那依然是「通过」，防线只存在于
> 「人会去读 summary 计数」这个假设里。现改为 HARD：无内容可查 → `INFEASIBLE` + 退出码 2。
> 注意它的语义与其它 HARD 不同：**这是「输入不合格」，不是「行程排不通」**，
> 处置方式是**检查文件层级**（要 `itinerary_*.json`），不是去调行程时间。
> 断言：`tests/test_evaluate_guards.py`。
>
> 返回值里还有 `coverage`（不进分数）：`activities_with_time_window` /
> `activities_with_source_time`。**score 只覆盖被填上的字段**——一条没填
> `opening_time` 的活动无论多离谱都触发不了 `BEFORE_OPENING`。
> 报告覆盖度是为了让「FEASIBLE 100」不被读成「方案优秀」。

> **`NEGATIVE_VALUE` 是 HARD 而非 WARNING，是方向决定的**：一个负的换乘时长会让
> 不可能的衔接看起来可行——把它当成"提示"就等于让引擎批准它本该拦下的东西。
> 出问题的值在后续评估中替换为 `0`，这样其余检查仍能跑完并一次性报出所有问题。

## 机票（`validate-flights`）

| Code | 级别 | 含义 |
|---|---|---|
| `INVALID_FLIGHT_LEG` | HARD | 航段字段缺失或格式错 |
| `INVALID_OFFER` | HARD | 报价结构非法 |
| `MISSING_PRICE` | HARD | 没有价格 |
| `INVALID_PRICE` | HARD | 价格解析不了 |
| `MISSING_SOURCE_METADATA` | HARD | 缺 channel 或 `checked_at` |
| `INVALID_SOURCE_TIME` | HARD | `checked_at` 解析不了 |
| `ARRIVAL_BEFORE_DEPARTURE` | HARD | 到达早于起飞 |
| `DURATION_MISMATCH` | HARD | 声明的时长与实际不符 |
| `PRICE_NOT_GUARANTEED` | WARNING | 网页可见价不是最终支付价，方案必须标这个限制 |
| `STALE_FLIGHT_PRICE` | WARNING | 超过 **2 小时**上限，**须重查** |
| `BAGGAGE_UNKNOWN` | WARNING | 行李额不明 |

> `STALE_FLIGHT_PRICE` 的意思是**重查这条报价，不是重新打个标签**。
> 车票价几乎不动，一天前的查询仍有参考价值；**今天早上的机票价就未必了**。
>
> 一条读错行的结果会产出"三小时航程却声称 80 分钟"的段，`DURATION_MISMATCH` 就是拦这个的。
> 而 `final_price_guaranteed` 在几乎每条网页结果上都是 false。

## 酒店（`validate-lodging`）

| Code | 级别 | 含义 |
|---|---|---|
| `INVALID_LODGING_OFFER` | HARD | 报价非法，或日期 / 价格基准无效 |
| `MISSING_SOURCE_METADATA` | HARD | 缺 channel 或 `checked_at` |
| `MISSING_LOGIN_STATE` | HARD | 完全没记录 `login_state` |
| `UNPRICED_LOGIN_STATE` | HARD | 记录的状态本就看不到价格（如未登录） |
| `INVALID_SOURCE_TIME` | HARD | `checked_at` 解析不了 |
| `MEMBER_TIER_UNRECORDED` | WARNING | 已登录但 `member_tier` 为空 |
| `STALE_LODGING_PRICE` | WARNING | 超过 **12 小时**上限，须重查 |
| `NO_FREE_CANCELLATION` | WARNING | 方案变动时这档价放不掉 |
| `MIXED_VIEWING_CONTEXTS` | WARNING | 报价横跨不同登录态或会员等级 |

> 酒店用 12 小时窗口而不是机票的 2 小时：房价变动比机票慢，
> **但节假日高峰的可订性不是**——所以是"更长"，不是"不设"。

---

# 七、交付层 · 路书事实源

这一层是本 skill 特有的（上游没有）：**事实源 JSON → HTML** 由 `render_html.py` 渲染，
`consistency.py` 拿它跟基准骨架比对。

字段规范见 `ludbook-schema.md`，模板见 `assets/路书事实源模板.json`。

**核心约定**：
- 事实源是**唯一真相**；HTML 只是渲染产物，**禁止反向从 HTML 改内容**
- 骨架的 `<style>` / `<script>` / IA / 导航由基准文件**原样带入**——
  "CSS 指纹一致"因此成为结构上不可能出错的事，而不是靠提示词祈求
- 每日必须有早/午/晚三行——「园内解决」「高铁上解决」**也要有行**
- 只写交通段、没有活动描述的行程无效

---

# 八、积累式情报资产 · sources.json 与玩法情报库

阶段 3.5 的三份可复用资产——**它们让「结合社媒生成攻略」从一次性动作变成持续提升质量**。
（线索卡的结构见上文 Social Research Result；此处定义另两份。）

## sources.json · 信源名单

使用者自建、跨行程积累（**本 skill 不预置任何白名单**）：

```json
{
  "updated": "2026-09-27",
  "sources": [
    {
      "platform": "小红书",
      "handle": "某账号",
      "id": "12345678",
      "followers": "12万",
      "topics": ["川渝美食"],
      "reliability": "medium",
      "note": "认号方法：读用户卡的账号ID+粉丝量比对内容形态"
    }
  ]
}
```

> 用平台榜单起步，旅途中刷到靠谱内容再补进来。

## 玩法情报库 · `情报库/玩法情报_<城市>.md`

阶段 6 复盘时追加的**人读沉淀**，与线索卡（机器可核）互补：

- 只沉淀**跨行程仍然成立**的东西——玩法、时节、机位、避坑；
- 会过期的（票价、档期活动）留在线索卡里，靠 `checked_at` 管时效；
- 每条带源与核实日期，格式见 `references/social-intake.md` §5。

> 下次去同城 / 邻近城市**先读它**，不重搜已知结论——这是「持续提升攻略质量」的落点。

---

# 九、消费方式

所有命令都输出结构化 JSON，**直接消费 JSON，不要用正则解析终端文字**。

```bash
CLI="python <SKILL_ROOT>/tools/travel_planner.py"

$CLI credential-status                    # key 配没配上、从哪读到的
$CLI doctor --live                        # 环境体检 + 实测高德
$CLI validate-request   --input trip_request.json
$CLI evaluate           --input itinerary.json            # INFEASIBLE 时退出码 2
$CLI validate-flights   --input final_plan.json --now "2026-09-27T13:00+08:00"
$CLI validate-lodging   --input final_plan.json --rooms 2
$CLI validate-plan      --input final_plan.json           # 证据不全时退出码 3
$CLI normalize-rail     --input rail_query.json --select
```

**退出码约定**：`0` 通过 ｜ `1` 执行出错 ｜ `2` 校验未通过 ｜ `3` 证据不全。

交付层另有两个专用脚本：

```bash
python <SKILL_ROOT>/tools/ludbook_check.py 路书_XX_v1.md --days 3 --json
python <SKILL_ROOT>/tools/render_html.py    路书_XX.json -o 路书_XX.html --verify
```
