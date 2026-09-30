# 第三方声明与使用边界

本 skill 是衍生作品。除了自身的 MIT 许可，还涉及以下上游与外部服务，
各自有独立的许可与使用条款。

---

## 一、上游开源项目（代码/规则来源）

本 skill 直接包含以下上游文件或其改写版，逐项列明以免责任边界模糊。

### awangwang123/jianhao-travel-planner

- 来源：<https://github.com/awangwang123/jianhao-travel-planner>
- 上游许可：MIT
- **直接随包的文件**：
  - `assets/情报卡_基准骨架.html` —— **原样保留**（含其 `<meta name="generator">` 标记）
    ｜ md5 `8ba0f2f68c6a…`
  - `assets/路书_基准骨架.html` —— **已改动**：在原骨架上补了**证据等级徽章**
    （`.ev` / `.ev-a|b|c|d` 四档配色与 hover 文案），这是本包「证据分级可见化」
    的落点。**副作用：版式指纹随之从 `9c3c6249a4` 变为 `cd440ea068`**
    （`consistency.py` 的指纹就是骨架 CSS 的 MD5，这是刻意设计——指纹是活真值，
    不硬编码）。2026-09-30 又补一处：打印块动效覆盖加 `!important`
    （动效特异性更高，不加则直接打印/导 PDF 丢正文），指纹变为 `63aa6072b6`。
    ｜ 当前 md5 `7718309fd556…`，21891 字节。
  - `tools/consistency.py` —— **已改动**（原样保留 + 一处修正）：占位符正则长度上限由
    24 放宽到 80。原上限会漏掉骨架页脚那条 40+ 字符的配图说明占位符，
    导致它即便残留在成品里也检查不出来。改动处在源码内已加注说明。
- 取用的机制与规则：交付质量机制、六维度结构、26 项自查骨架、三池归属、
  双写制、脱敏双轨化思路
- 完整版权声明见 [`LICENSE`](LICENSE)

### tanweiping1012-source/travel-planner

- 来源：<https://github.com/tanweiping1012-source/travel-planner>
- 上游许可：MIT
- **直接随包的文件**（`engine/travel_planner/`）：
  - **原样迁移（7 个）**：`timeutil.py` ｜ `models.py` ｜ `flight.py` ｜
    `lodging.py` ｜ `rail.py` ｜ `research.py` ｜ `geomatch.py`
  - **已改动（5 个）**：
    - `intake.py` —— 新增采集字段 `planned_places`（用户**已计划前往**的地点，
      带 `BOOKED`（已订票，时间窗固定）/ `INTENDED`（只是想去）两档状态）、
      对应校验，以及与 `excluded_places` 的冲突检查。上游只采 `must_visit`（必去），
      拿不到「用户心里已经定了要去哪几个点」这一层。
      另新增软字段 `preferences`（用户**自报**的饮食 `diet` / 作息 `pace` / 兴趣
      `interests`，供主路线个性化；缺省进 `assumptions`，只采集原话、不推断）。
    - `amap.py` —— 补**退避重试自适应**（限流类 infocode 与网络错误才重试，
      指数退避带抖动，默认 3 次 / 上限 8s；新增 `AmapNetworkError` 继承 `AmapError`
      以保持既有 `except` 兼容）。上游在 QPS 限流下会直接失败。
      2026-09-30 又补：`call_log` 留痕——每次成功调用记录 endpoint、脱敏入参
      （key 绝不入）、时刻与完整返回体；失败与重试中间态不留（不构成证据）。
      这是 P1「来源留痕」的采集侧落点，上游无此能力。
    - `workflow.py` —— `collect_amap_snapshot` 新增 `keep_raw` 参数（默认 True）：
      把 `AmapClient.call_log` 内嵌进快照 `raw_calls`，`provenance` 增
      `raw_retained` / `raw_call_count`；`keep_raw=False` 时连键都不写，
      让「没留」与「留了但为空」可区分。上游快照只有蒸馏值、无原始返回可比对。
      2026-09-30 又落地**路线并发采集**（暂缓项）：`_collect_routes` 用
      `ThreadPoolExecutor` 并发采 transit/driving（worker 封顶 2，顺序与错误
      隔离语义与串行一致，QPS 由自适应退避兜底）。上游为纯串行循环。
    - `feasibility.py` —— `EMPTY_ITINERARY` 由 WARNING **提为 HARD**：
      `activities` 为空时判 `INFEASIBLE` 并返回退出码 2。上游只给警告，导致
      「什么都没查」与「全部通过」在退出码上不可区分。
      2026-09-30 又做**纯结构重构**：429 行单函数 `evaluate_itinerary` 拆为八个
      阶段函数，代码逐段原样搬移、append 顺序不变——评估报告在固定 `--now` 下
      逐字节一致（5 份输入实测，含 INFEASIBLE / EMPTY / 每日跨度路径）。
    - `diagnostics.py` —— 扩展环境自检分支（zcode / workbuddy 配置路径）并区分
      「配置文件为空」与「配置文件损坏」。上游只报存在性。
- **`credentials.py` 为改写版**：上游只支持 macOS 钥匙串（用 subprocess 调
  `security` 命令），在 Windows 上不可用。本版改为「环境变量 → 本地凭据文件 →
  macOS 钥匙串」三级回退，并保证非 macOS 平台不加载 subprocess。
  类名 `KeychainCredentialStore` 保留为别名以兼容上游用法。
- **`tools/travel_planner.py` 为派生文件**：迁移自上游 `scripts/travel_planner.py`，
  做了六处适配 ——
  ① 引擎路径由 `src/` 改为 `engine/`；
  ② 凭据改用本 skill 的跨平台版本（上游那版在 Windows 上取不到任何值）；
  ③ `doctor` 命令并入本机能力检查（tzdata / 随包资产 / 引擎），
     与上游的 rail · browser 检查合并为一份报告；
  ④ `evaluate` 在 `INFEASIBLE` 时返回非零退出码——上游只打印报告就正常退出，
     调用方拿不到"排不通"这个信号，而那是本 skill 的硬闸门。
  ⑤ 新增 `nearby-spots` 命令（上游没有）：沿主路线各站走高德周边搜索，采「顺道可去」
     的候选点，供事实源的 `days[].nearby` 用。只列不判断——值不值得进由人定。
     （2026-09-30 补记：本条加入时忘把「四处」改成「五处」，被自指校验的对账习惯抓出——
     说明文字里的计数也是声明。）
  ⑥ 新增来源留痕出口（2026-09-30，P1）：`amap-snapshot --no-keep-raw`、
     `search-places --trace` / `nearby-spots --trace`——留痕默认开启、可显式放弃。
  命令集合与参数语义保持与上游一致，未删减。
- **`tools/doctor.py` 为自写**，沿用上游 `diagnostics.py` 的"能力先测后报"思路，
  但检查项换成本机实际需要关心的（时区数据库、随包资产、引擎可导入）。
- 取用的机制与规则：证据分级机制、时戳纪律、能力自检与降级、只读边界、
  "试一次再报"原则
- 完整版权声明见 [`LICENSE`](LICENSE)

> **两个上游均为 MIT**，允许商用、修改、再分发，条件是保留版权声明与许可声明。
> 本 skill 已在 `LICENSE` 中完整保留二者原文，并在 `LICENSE` 与本节逐项标明
> 哪些文件原样引用、哪些做过改动、改了什么。
>
> **上游许可已逐字核对（2026-09-28）**：拉取两仓库 `LICENSE` 原文比对，
> 均为**标准 MIT 全文、无附加条款、无「仅限学习/禁止商用」类限制**
> （copyright 分别为 `2026 awangwang123` 与 `2026 travel-planner-mvp contributors`）。
> 唯一带「不用于商业用途」声明的是上游用到的**铁路社区连接器**，而本 skill
> **未搬入**该连接器（已定性「不接入」，见 `SKILL.md` 外部依赖一节）。

### 归属核对表（17 个上游文件 · 机器校验）

<!-- 下面三行是**被 validate_skill.py 规则校验的声明**，改动前先读规则表：
     「上游原样文件数」「台账与磁盘不一致行数」「LICENSE 归属标注不一致处数」。 -->

- **原样文件：8 个**（这些文件一个字节都不该动；动了哈希立刻对不上）
- **台账核对：与磁盘不一致 0 行。**
- **LICENSE 归属标注：与台账不一致 0 处。**

| 文件 | 归属 | md5（12 位） | 字节 |
|---|---|---|---|
| `engine/travel_planner/timeutil.py` | 原样 | `951d65310eae` | 1761 |
| `engine/travel_planner/models.py` | 原样 | `c7b3e5160bc7` | 1586 |
| `engine/travel_planner/intake.py` | **已改动** | `5e1699edbb83` | 14249 |
| `engine/travel_planner/flight.py` | 原样 | `3c97aca4e08a` | 12073 |
| `engine/travel_planner/lodging.py` | 原样 | `581b2c4c2cd2` | 10284 |
| `engine/travel_planner/rail.py` | 原样 | `78bf2c406ee4` | 11001 |
| `engine/travel_planner/research.py` | 原样 | `a00ad262ada4` | 11160 |
| `engine/travel_planner/geomatch.py` | 原样 | `a019684848d5` | 3689 |
| `engine/travel_planner/workflow.py` | 已改动 | `cfff74c76491` | 4970 |
| `assets/情报卡_基准骨架.html` | 原样 | `8ba0f2f68c6a` | 18982 |
| `engine/travel_planner/amap.py` | 已改动 | `80ec6c9e2d42` | 22341 |
| `engine/travel_planner/feasibility.py` | 已改动 | `70d195e350d0` | 25115 |
| `engine/travel_planner/diagnostics.py` | 已改动 | `691f7389a5ec` | 14199 |
| `engine/travel_planner/credentials.py` | 已改动 | `060922eddbd5` | 5838 |
| `assets/路书_基准骨架.html` | 已改动 | `7718309fd556` | 21891 |
| `tools/consistency.py` | 已改动 | `4d3247dca8f6` | 10471 |
| `tools/travel_planner.py` | 已改动 | `51c19680bb2e` | 27470 |

> **这张表现在是「核对」而不只是「声明」**：`validate_skill.py` 会读本表逐一比对
> 磁盘上的 md5 与字节数，并比对 `LICENSE` 里的归属标注是否与本表一致——
> 任何一处不符即 **FAIL**。所以改过表中任何文件（哪怕是「已改动」那一档），
> 都要回来更新这一行，否则自指校验会当场报红。
>
> **它防的是真事**：2026-09-28 就发现声明为「原样」的 4 个文件其实被改过
> （`amap.py` / `diagnostics.py` / `feasibility.py` / `路书_基准骨架.html`），
> 而当时的 `validate_skill` 45/45 全绿**完全没抓到**——因为它不覆盖这一类声明。
> 这条规则就是那次漏检的产物。

---

## 二、外部数据服务（使用者自备凭证，不随包分发）

### 地图服务

- 通过使用者自备的 API key 访问
- 使用受该平台的服务条款、配额、数据展示要求与隐私规则约束
- **本仓库不包含任何 API key 或平台数据**
- 各地图服务对境外地点的返回行为需使用者自行验证（存在"同名替换"风险，
  见 `references/evidence-rules.md`）

### 铁路车次查询

- 上游项目使用的是社区维护的第三方 MCP 连接器，**不是官方开发者 API，
  也不是授权的售票渠道**
- 上游项目对该连接器的描述为"学习/研究用途"，并声明不用于商业用途
- **使用前请自行审阅上游许可、README、平台服务条款及适用法律**

### OTA / 社交平台调研

- 通过浏览器读取公开可见页面
- **本 skill 不授予任何数据采集权利**
- 使用者需自行承担平台条款、账号规则、著作权、隐私与访问频率限制的责任
- **不得通过本 skill 再分发**：抓取的笔记正文、评论、图片、视频、
  个性化价格或账号数据

> 🔴 上游项目明确要求：不得批量抓取平台内容。本 skill 沿用该边界。
> 高频抓取可能违反平台规则，也可能触犯相关法律。

---

## 三、本 skill 的使用边界（合规红线）

本 skill 是**只读**的。以下行为被明文禁止，且写入了 `SKILL.md` 铁律 8：

| 禁止 | 说明 |
|---|---|
| 购买 / 预订 / 支付 | 无下单能力 |
| 候补 / 改签 / 退票 | 无票务操作能力 |
| 输入密码 / 验证码 / 身份证 / 支付信息 | 登录与验证码永远交还用户 |
| 导出 Cookie / Token / 浏览器会话 | 不触碰凭证 |
| 点赞 / 收藏 / 关注 / 评论 / 发布 | 不修改任何外部账号 |
| 绕过验证码 / 登录墙 / 风控 | 遇阻即停，换路径 |
| 批量抓取平台内容 | 不做高频抓取 |

---

## 四、免责声明

- **本 skill 产出的路书仅供参考。** 所有动态信息（票价、车次、营业时间、
  天气）都可能变化，**出行前请以官方渠道为准自行复核**。
- 本 skill 通过证据分级机制标注信息可信度，但**不保证任何信息的准确性**。
- 使用者应自行判断并承担出行决策的风险。安全和开心，才是最重要的。

---

## 五、商用提示

如计划商用：

1. 确认两个上游的 MIT 条件已满足（保留版权声明）
2. 查阅所用地图/铁路/OTA 服务的最新条款与配额
3. 确认对社交平台内容的抓取与再分发符合平台规则与当地法律
4. 铁路查询所依赖的社区连接器明确声明不用于商业用途，商用前必须另行评估

---

## 六、示例资产来源（图片）

**结论：本仓库不包含任何第三方图片素材。** 示例与资产目录里的图形全部自绘，
可随本包自由再分发。

| 资产 | 位置 | 来源 | 状态 |
|---|---|---|---|
| 4 张配图（宽窄巷子 / 川西民居 / 熊猫 / 竹径） | `../产出示例/imgs/*.png` | **自绘文字占位图**（纯色背景 + 标题文字 + 「本地单文件版·断网可开·占位图」） | 自绘，无第三方权利 |
| 3 张示意图 | `../产出示例/东莞/imgs/d*.svg` | 自绘 SVG，caption 已标「示意」 | 自绘，无第三方权利 |
| 基准骨架里的图标 / 样式 | `assets/*.html` | 上游项目自带，见第一节 | 随上游 MIT |

> **这一段是一次自查的产物（2026-09-28）**：当时成都事实源的 `photos_source` 写着
> **「Pexels 免费图库」**，而磁盘上那 4 张其实是**自绘文字占位图**——声明与实际不符，
> 而且这句话会被渲染进交付物（成品里直接印着「图源 Pexels 免费图库」）。
> 已改为如实描述：`自绘文字占位图（非实拍、非图库素材）`，并在此逐项记录。
>
> **为什么值得记下来**：本项目的立身之本是「每个数字都有来源、不骗人」，
> 而这次恰恰是**自己的示例里有一句不实的来源声明**。它没被任何闸门抓到，
> 因为现有六道闸门都不审「资产出处」——这也说明闸门覆盖面的边界在哪。
> 如果将来重新引入真实图库素材（Pexels / Unsplash 等），**必须回到本表登记**，
> 并按其许可条款（多数允许免费使用但禁止转售原始素材本身）评估再分发范围。
