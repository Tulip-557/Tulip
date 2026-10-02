# 旅游规划器（可核验版）· 项目工作区

> 迁移自本机 skills 目录（`~/.workbuddy/skills/verified-travel-planner`）  
> 迁移时间：2026-09-27　状态：**核心链路已跑通，可直接使用**

> **仓库公开范围**（要发到 GitHub 前请先看这张表）
>
> | 路径                                                                   | 是否入库      | 说明                                                            |
> | -------------------------------------------------------------------- | --------- | ------------------------------------------------------------- |
> | `verified-travel-planner/`                                           | ✅ 公开      | **产品本体**：可独立分发的 skill，自带 `LICENSE` + `THIRD_PARTY_NOTICES.md` |
> | `产出示例/`                                                              | ✅ 公开      | 示例产出（东莞完整交付链路 / 成都渲染基线）                                       |
> | `skill-doc-code-audit/`                                              | ✅ 公开      | 配套的文档↔代码审计小工具（另一个自建技能）                                        |
> | `README.md` / `AGENTS.md` / `CONTRIBUTING.md` / `LICENSE` / `LICENSE.guide.md` / `PUBLISHING.md` / `.github/` / `assets/`（README 主图）/ `docs/`（Pages 落地页） | ✅ 公开      | 说明、许可、贡献指南、发布指南、在线示例入口与 CI/Pages        |
> | `.workbuddy/`                                                        | 🔒 **私有** | 会话记忆与发布登记：含本机路径、内网地址、一条已上线的路书链接                               |
> | `验收记录/`                                                              | 🔒 **私有** | 过程留痕：迁移 / 多端联接的一次性脚本，路径写死本机                                   |
> | `项目介绍*.html / 项目说明*.md`（根目录 4 个文件）                         | 🔒 **私有** | 用户自用参赛/介绍材料，2026-09-30 起入 `.gitignore`，勿提交                |
> | `~/.verified-travel-planner/`（**不在仓库内**）                             | 🔒 **私有** | 高德 key 与登录态 profile，机器级凭据                                     |
>
> 私密项**保留在本地磁盘**，靠 `.gitignore` + 取消跟踪隔离；公开前还必须处理  
> **git 历史里已有的副本**与**提交署名**——两步的操作与实测踩坑见 `PUBLISHING.md`。

---

## 这是什么

一个「旅行规划 Agent Skill」，融合了两个开源项目的长处：

| 来源                        | 负责什么                                     |
| ------------------------- | ---------------------------------------- |
| **见好旅行规划器**（jianhao，MIT）  | 交付得像样——30 项交付自查、六维度玩法、三池归属、双写制、脱敏、去 AI 味 |
| **tanweiping1012** 的实证派做法 | 数字不骗人——证据四级分级、时戳纪律、能力先测后报、只读边界           |

**它的核心主张**：路书里每个数字都有来源、每处结论都能被机器校验。

**三条铁律**：

1. **证据分级**：`[A]` 工具实证 → `[B]` 多源交叉 → `[C]` 单源线索 → `[D]` 未核实。无地图能力时，POI 与距离时长**一律标 `[D]`**，不得用模型推断的坐标冒充已核实。
2. **排不通不给**：`evaluate` 做确定性可行性检查（时间窗 / 换乘余量 / 营业时间 / 预算闭合），`INFEASIBLE` 直接退出码 2，不交付。
3. **只读边界**：不购买、不预订、不支付、不候补、不改签；不输入密码 / 验证码 / 身份证 / 支付信息。

---

## 和同类项目比，强在哪

2026-09-27 对标了 GitHub 上 6 个同类项目（详见 `verified-travel-planner/references/landscape.md`）。  
读完最该记住的是**别人没有的四样东西**：

|              | 本包                                                    | 同类项目常见做法                                                       |
| ------------ | ----------------------------------------------------- | -------------------------------------------------------------- |
| **数字可信度**    | 证据四级 `[A]/[B]/[C]/[D]` + 四条死线 + **九道闸门**，**机器可判**      | 票价与玩法混着写，无分级——6 个对标项目里 **0 个**有等价机制                            |
| **没数据时怎么办**  | 标 `[D]`，**绝不猜**                                       | 常见做法是「API 失败就回退 LLM 知识库」——`travel-guidebook`(89⭐) 把它写进 FAQ 当卖点 |
| **文档与代码一致性** | `validate_skill.py` 机器校验，连它自己声明的「19 条规则 / 81 处断言」也一起查 | 无                                                              |
| **离线可用性**    | 单文件 HTML，配图 base64 内联，**0 个外部依赖**，可整文件 MD5 复现         | 多依赖 Google Fonts / CDN 图标，断网即降级                                |

本质差别在第二行：**同类项目把「可用性」放在「可信度」之前，本包相反。**

---

## 目录结构

```
D:\旅游\
├── README.md                       ← 你正在读的这份
├── AGENTS.md                       ← ★ AI 上下文入口（ZCode / Codex / Cursor 等读这个）
├── .gitignore                      ← 防误提交凭证/缓存
├── .workbuddy\memory\              ← 🔒 私有，不入库
│   ├── 2026-09-27.md               ← 开发全程日志（WorkBuddy 打开本工作区会自动加载）
│   └── MEMORY.md                   ← 项目长期记忆（精炼版，同样自动加载）
├── verified-travel-planner\         ← ★ 项目主体（技能源码）
│   ├── SKILL.md                    ← 主工作流：铁律、阶段 0-6、阶段 4.5 随行套件
│   ├── LICENSE                     ← 保留上游 MIT 版权声明（合规必需）
│   ├── THIRD_PARTY_NOTICES.md      ← 两个上游的完整版权声明
│   ├── engine\travel_planner\       ← 检查引擎（14 个模块，纯标准库）
│   │   ├── feasibility.py          ← 可行性判定核心
│   │   ├── amap.py / rail.py / flight.py / lodging.py
│   │   ├── weather.py              ← 天气覆盖判定（预报窗口仅 4 天，属临行前能力）
│   │   ├── credentials.py          ← 凭证三级回退（跨平台改写）
│   │   └── ...
│   ├── tools\                      ← 命令行工具
│   │   ├── travel_planner.py       ← ★ 统一 CLI（14 个命令）
│   │   │                             含 weather：实况 + 4 天预报 + 「哪天超出窗口」
│   │   ├── render_html.py          ← 渲染器（事实源 JSON → 路书 HTML，数据模板分离）
│   │   │                             同一命令加 --compact 出「精简执行版」
│   │   ├── consistency.py          ← 版式一致性校验（以基准骨架为真值）
│   │   ├── ludbook_check.py        ← 30 项交付自查（含 ㉗ 社会情报已声明）
│   │   ├── freshness.py            ← 时效体检（动态数字到出发日还新不新，按 6 类阈值判）
│   │   ├── source_audit.py         ← 源核验闸门（徽章标注纪律：时戳/双源/死线/出路/闭环）
│   │   ├── claim_audit.py          ← 声明↔留痕比对闸门（P1 v1：实采值必须如实进路书）
│   │   ├── compact_draft.py        ← compact 半自动草稿（写作期助手，永不写事实源）
│   │   ├── social_notes.py         ← 社会情报线索归一化 + 死线校验 + 增量合并
│   │   │                             --triangulate 多来源交叉印证 / 口碑构成
│   │   ├── social_source.py        ← 社媒采集渠道能力矩阵（21 条五档）+ 采集计划
│   │   ├── social_login.py         ← 用户授权登录态采集探针（AUTH 档：--check/--launch/--grab）
│   │   ├── cdp_read.py             ← CDP 只读读取器：滚动触发懒加载 + 穿透 Shadow DOM
│   │   │                             内置最小 WebSocket 客户端，零第三方依赖
│   │   ├── validate_skill.py       ← 文档计数断言校验（19 条规则 / 81 处断言，可进 CI）
│   │   ├── shoot.py                ← 无头浏览器四档溢出探针 + 截图
│   │   ├── set_amap_key.py         ← 高德 key 配置（跨平台）
│   │   ├── doctor.py / desource.py
│   ├── references\                 ← 渐进加载的参考文档（用到才读）
│   │   ├── portability.md          ← 跨设备可复现：哪层依赖本机 / 发布打包要求
│   │   ├── landscape.md            ← 同类项目对标与差距评估：该补什么 / 明确不补什么
│   │   ├── source-audit.md         ← 源核验：标注纪律 8 条规则 / 判定设计 / 示例定位
│   │   ├── social-credibility.md   ← 口碑可信度：评价与评论区 / 夸大与水军信号 / 三角验证
│   │   ├── transcript-and-comments.md ← 口播与评论获取：怎么拿 / 拿到什么 / 红线在哪
│   │   └── social-intake.md / social-sources.md / social-login.md / experience.md …
│   └── assets\                     ← 基准骨架与事实源模板
├── skill-doc-code-audit\            ← 配套工具：核查「文档声称的」与「代码实现的」是否一致
├── 产出示例\
│   ├── 东莞\                        ← **完整交付链路示例**（首个九道闸门全流程跑通）
│   │   ├── 路书_东莞.json / .html / _精简版.html
│   │   ├── itinerary_东莞.json / final_plan_东莞.json / trip_request.json
│   │   └── imgs\ 3 张本地 SVG 示意图
│   ├── 路书_成都_事实源.json        ← 回归基线的数据源（改代码后用它验证可重现性，勿动）
│   ├── 路书_成都_渲染示例.html      ← 回归基线成品（配图已内联，断网可开）
│   │  ⚠️ 它只作**版式回归**用，不是完整路书——跑内容层 30 项自查会红 ②⑮⑯㉗ 四项，**那是预期**
│   ├── imgs\                       ← 成都配图 4 张（从成品 HTML 反解还原）
│   └──（中山示例已撤下：2026-09-27 的实验性产出，完整保留在 git 历史 `3d1b73a`，
       含 86 枚证据徽章的完整范例 / 精简版 / 社媒线索实测；2026-09-28 已连同其线上
       发布链接一起下线，本地发布副本与发布标记亦已清除）
└── 验收记录\                       ← 🔒 私有，不入库（过程留痕，不是产品）
    ├── snapshot_input.json         ← 高德采集入参
    ├── snapshot_out.json           ← 采集结果（广州→珠海，含坐标/路线/周边）
    ├── 迁移脚本\                    ← 本次迁移用到的脚本（可追溯：聚合 / 校验 / 反解配图 / 终验）
    └── 多端联接脚本\                ← 配置「唯一真身 + 多端入口」时的脚本（可追溯）
```

标 🔒 的两项**只存在于本地**：`git clone` 这个仓库拿不到它们，这是设计意图——  
它们记的是「这台机器怎么干活的」，还含上线链接与内网地址。

---

## 快速开始

**Python**：任意 Python 3.9+ 都行（本机实测 3.13.12；直接 `python` 或用你本机的绝对路径）  
（引擎纯标准库，但 Windows 需装 `tzdata`——**必须用阿里源**，清华源对该包查无此包）：

```bash
pip install tzdata -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
```

**常用命令**（在 `verified-travel-planner\` 下执行）：

```bash
# 看全部 14 个命令
python tools\travel_planner.py --help

# 环境体检（自带客户端要显式指定，否则会猜错）
python tools\travel_planner.py doctor --client workbuddy

# 查 POI（需 key）
python tools\travel_planner.py search-places --keywords "宽窄巷子" --city 成都

# 采集坐标+路线+周边（需 key；起终点必须是【城市级】，POI 会被门禁拒绝）
python tools\travel_planner.py amap-snapshot --input 输入.json --output 快照.json

# 可行性检查（排不通则退出码 2）
python tools\travel_planner.py evaluate --input 行程.json

# 渲染路书（数据与模板分离）
python tools\render_html.py 事实源.json -o 路书.html

# 精简执行版（同一份事实源，零 CSS 改动，版式指纹不变）
python tools\render_html.py 事实源.json -o 路书_精简版.html --compact

# 版式一致性校验
python tools\consistency.py 路书.html --base assets\路书_基准骨架.html

# 时效体检：这些数字到出发日还新不新（--strict 有须重查项则退出码 2）
python tools\freshness.py 路书_事实源.json --on 2026-09-28

# 天气（需 key；预报窗口仅 4 天 —— 属临行前能力，城市名要写全或直接给 adcode）
python tools\travel_planner.py weather --city 中山市 --start 2026-09-28 --end 2026-09-30
```

⚠️ 高德**天气接口**要求**完整行政区名**或 adcode：写「中山」带 `all` 会报 20003、  
带 `base` 会**静默返回「大连市中山区」**。最稳是给 adcode（广东中山市 `442000`）。  
同类地名陷阱在路线接口上是**主动拒绝**（覆盖门禁），在天气接口上是**静默出错**——  
所以这条命令对空返回、名称不符、区县级同名三种情况**一律报错并退出码 2**，不给你一张安静的空表。

**高德 key**：放在用户主目录的 `~/.verified-travel-planner/credentials.json`  
（不在此项目内，属机器级凭证，请勿拷贝进版本库）。  
⚠️ 别设 `AMAP_API_KEY` 环境变量——它会遮蔽凭据文件。  
⚠️ 个人认证配额：关键字/周边搜索约 **100 次/日**，地理编码与路径规划 5000 次/日，每日 0 点重置。

---


## 当前状态

**已完成（A 级全落地）**

- 融合双核机制（见好交付质量 + 实证派数据纪律）
- 检查引擎 14 模块迁移 + 统一 CLI 14 命令
- 渲染管线：事实源 JSON → 单文件 HTML（配图 base64 内联，断网可开）
- 合规：LICENSE + THIRD_PARTY_NOTICES（保留两个上游版权声明）
- 文档 9 章契约（`references/data-contracts.md`）
- **高德 key 已配置并通过 4 步验收**（凭证 / 联网 / POI / 快照采集）
- 环境体检 8 项（Python / 时区 / 引擎 41 符号 / 随包资产 9-9 / **第三方依赖 0** /  
  **平台绑定项** / 机器级私有状态 / 高德 key）——核心项全 `READY`
- 产出示例可完整重新渲染（本次迁移中补齐了缺失的配图目录）

**2026-09-27 13:22 承上复核（新会话重开工作区后实测）**

| 检查项     | 命令                                    | 结果                                                            |
| ------- | ------------------------------------- | ------------------------------------------------------------- |
| 环境体检    | `doctor.py`                           | 8 项：核心全 `READY`；**零第三方依赖**（34 个标准库模块）、平台绑定项已实测、机器级私有状态如实列出    |
| 高德凭证    | `travel_planner.py credential-status` | `CONFIGURED`，来源凭据文件，仍然有效                                      |
| 端到端渲染   | `render_html.py 产出示例/路书_成都_事实源.json`  | 与示例成品**整文件 MD5 一致**（当日为 `49292d62…`；2026-09-28 示例中性化 + 图源声明修正后重建为 `baea7a0a…`；2026-09-30 打印块修复后为 `df75e371…`），逐字节可重现 |
| 版式一致性   | `consistency.py --days 3`             | 全绿（指纹 `63aa6072b6`、区块 13、导航 15；成都 figure 4 / 中山 figure 3）     |
| 证据分级落地  | `ludbook_check.py --html`             | 中山 **86 枚徽章**（A26/B20/C25/D15，v2 并入社媒情报后），第 ② 项已从提示级升回**阻断级** |
| 多端联接    | 读 `st_reparse_tag`                    | 4 个入口均为 junction，全部指向 `D:\旅游\`                                |
| 文档↔实现审计 | `skill-doc-code-audit/audit.py`       | 路径与命令引用全部存在，退出码 0                                             |

**2026-09-27 22:00 对标后新增（本次）**

| 新增             | 命令                                     | 实测                                                                       |
| -------------- | -------------------------------------- | ------------------------------------------------------------------------ |
| **时效体检**       | `tools/freshness.py 事实源.json`          | 中山：**须重查 3 条（全是门票）**、路线 14 条折叠成一行、无时戳 6 条；负向样本 0 须重查，`--strict` 退出码 2 正确 |
| **天气**         | `travel_planner.py weather --city 中山市` | 实况 27℃ 晴；预报 4 天；**9/30 白天晴、夜间雷阵雨**被自动标出（正是东堤日落那天）                        |
| **对标评估**       | 读 `references/landscape.md`            | 6 个同类项目逐条比对，产出「该补什么 / 明确不补什么及理由」                                         |
| HTML 模式基准日 bug | 同 `freshness.py`                       | 修前静默回退「今天」→ 全部假绿；修后从 `day-idx` 恢复出发日，与 JSON 模式结论一致                       |

→ 结论：**项目可原样续接，无需修复**；铁路 MCP 一项 2026-09-27 已定性为「**不接入**」  
（市场无 12306 官方 server，详见 `SKILL.md` 外部依赖），不再是待办；浏览器一项已由  
`tools/shoot.py` + `references/browser-use.md` 落地。

> **2026-09-28 更正**：上句原先写的是「`overall: PARTIAL` 只因铁路 MCP 一项」。  
> 该措辞已过期——`doctor.py` 现在只输出 `checks` + `ok` 两个键，**不再有 `overall` /  
> `PARTIAL` 这套口径**（铁路也不在它的检查项里）。当前体检为 **8 项**：Python 解释器 /  
> 时区数据库 / 检查引擎（3 项 critical）+ 高德地图 key / 随包资产 / 第三方依赖 /  
> 平台绑定项 / 机器级私有状态，核心全 `READY`，`ok: true`。

> ✅ **2026-09-27 已补上**：`references/experience.md` 的「本机记录」区已写入首个真实行程的 7 条教训  
> （中山 3 天 2 晚）。这套 skill 已跑通一次完整实战，不再是零实战。

**待办（2026-09-27 17:50 更新）**

- [x] **P0 · 建 git 仓库并首次提交**——`产出示例/` 已入库，回归基线不再悬空
- [x] **P0 · 统一事实源口径为 JSON**——`SKILL.md` 4 处（description / 铁律 3 / 第 25 项 / 阶段 6）
- [x] **P0 · 「15 模块」改 13**——`AGENTS.md:40`、`README.md:41`、`README.md:115`  
  （**2026-09-28 更正：13 本身是数错的，实测为 14**——`engine/travel_planner/` 除  
  `__init__.py` 共 14 个模块。当时把 `timeutil.py` / `models.py` 之类漏数了。  
  现 `README.md:167` 与 `AGENTS.md` 均已为 14，本条的 13 只作历史留痕）
- [x] **P1 · `ludbook_check.py` 补 A/B 组进 HTML 模式**——机械判定由 4 项提到 19 项，运行结尾打印覆盖声明
- [x] **P1 · 交付分发写进 `SKILL.md`**——新增阶段 5.5，首选微信传文件（唯一实测有效）
- [x] **B 级 · `browser-use.md` + `tools/shoot.py`**——浏览器核验能力已落地：四档溢出探针 + 截图，  
  并记下两个陷阱（无头浏览器最小视口约 496px 会原地裁剪、截图不滚动致动效未触发）
- [x] **B 级 · `client-compatibility.md`**——已建成 `references/client-compatibility.md`  
  （客户端兼容矩阵；证据分「实测 / 据实现」两级，未核实的一律明写）
- [x] **B 级 · `validate_skill.py`**——已建成 `tools/validate_skill.py`，机器校验文档里的计数断言
- [x] **B 级 · 12306 铁路 MCP**——**已定性为「不接入」**。2026-09-27 实测：可获取的 MCP 市场里  
  **没有 12306 官方 server**，只有第三方 OTA／差旅聚合（同程程心、途牛等），接上也只能算 `[B]`；  
  且本包**从未搬入**上游的 `setup_rail_mcp.sh` / `rail-mcp.md` / `patches/`，而 `doctor`  
  却指引用户去运行它——该虚假指引已改成如实说明。铁路项默认走只读网页 `[C]` 或标 `[D]`。
- [x] **长期缺口 · 证据标记工具链**——三处补齐：事实源模板示范写法、`render_html.py` 把  
  `[A]/[B]/[C·单源]/[D]` 转成 `.ev` 徽章、基准骨架加四档样式。第 ② 项自查由提示级  
  升回**阻断级**；中山实战 86 枚徽章（A26/B20/C25/D15）。骨架 CSS 变更使版式指纹  
  `9c3c6249a4` → **`cd440ea068`**，两份示例已重渲染并同步（成都基线当日为 `49292d62…`；
  2026-09-28 示例中性化 + 图源声明修正后重建为 `baea7a0a…`）。
- [x] **B 级 · 社媒情报采集**——已建成 `references/social-intake.md` + `tools/social_notes.py`。  
  实测结论：本机**直连小红书/抖音被风控拦**（`安全风险 300012` / `验证码中间页`），  
  故走「官方文旅发布 → 搜索索引 → 用户投喂」三条路；工具把三条死线做成机器判定  
  （六类证据类别、价格只能 `PRICE_SIGNAL` 且须标未核实、营业时间与车次余票一律拒）。  
  示例：中山实测 8 张完整景点卡、反向验证 9 项违规全部被拦（线索文件已随示例归档，见 git `3d1b73a`）。
- [x] **2026-09-27 19:50 · 跨设备可复现**——新增 `references/portability.md` + `doctor` 三项体检  
  （第三方依赖 / 平台绑定项 / 机器级私有状态）。实测**零第三方依赖**（34 个标准库模块），  
  核心闸门换台机器 clone 下来即可跑；只有**高德 key** 与**登录态 profile** 必须自建  
  （两者都在 `~/.verified-travel-planner/`，`.gitignore` 已隔离，不存在误推路径）。
- [x] **2026-09-27 19:50 · 口碑层（评价 / 评论区 / 水军）**——`social_notes.py` 新增  
  `voice` 分型（官方 / 媒体 / 编辑 / 创作者 / 游客 / 本地人 / 评论区）、`reviews[]`  
  （含评论区，`stance` 人工登记、机器不判语义）、夸大词与三判据水军启发式、  
  `--triangulate` 多来源交叉印证；配套 `references/social-credibility.md`。  
  中山实测的**诚实结论**：13 个地点**交叉印证 0 个**、口碑构成「编辑 3 / 媒体 4」，  
  **真人评价与评论区为 0**——因此路书里不得表述为「游客普遍反映」。  
  \> 水军与夸大一律只出**信号**不出结论，每条带 `water_disclaimer`；  
  \> 正负向已验：3 条雷同评论全部命中，而「停车位找了 20 分钟」这类有细节的差评一条不误伤。
- [x] **2026-09-27 19:50 · 完整版 + 精简版**——`render_html.py --compact`。同一事实源、  
  同一骨架出两版：**`sections` 与 `days[].slots` 都可写 `full`/`compact` 双档**  
  （**写在同一字段**，杜绝双写不同步）+ 首屏「一眼看全 N 天」速览表（带 `#dayN` 跳转锚点，  
  专治「看到一半忘了前文」）+ 每天 1 张配图。  
  **2026-09-27 23:30 重做**：首版把双档只做在 `sections` 上，而实测 `slots` 占全篇 **65%** ——  
  机制装错了地方，全篇只省 15%。改后四轮削减实测 **97% → 83% → 62% → 56% → 55%**，  
  中山精简版 **7837 字 / 完整版 14072 字的 55.7%**（`compact_check` C5 全文口径；  
  按「只数 section 正文」的探针口径是 7782 字 / 55.3%——两个口径别混用）。  
  新增精简版专用闸门 `compact_check.py`  
  （C1 双档覆盖 / C2 逐段精简度 / C3 约束不丢 / C4 证据不降级 / C5 产物精简率）。  
  规范见 `references/dual-version.md`。完整版**逐字节零副作用**，两版**九道闸门全绿**。
- [x] **2026-09-27 22:00 · 时效体检（补规范与数据的断层）**——新增 `tools/freshness.py`。  
  `evidence-rules.md` 定义了 6 类时效阈值（机票 2h / 火车票 24h / 酒店 12h / 门票当天 /  
  营业时间 30天 / 路线当日），但**事实源里只有 1 处时戳，且没有任何命令能查**——规范写了、数据没落。  
  工具扫正文里带时戳的动态声明，按类型套阈值，以**出发日**为基准判 `FRESH` / `MUST_RECHECK`。  
  中山实测：**须重查 3 条（全是门票）**，路线类折叠成一行（19 条噪声 → 3 条要看的）。  
  修掉一个**假绿 bug**：HTML 模式缺 `date_range` 时静默回退「今天」→ 全判新鲜；改为从 `day-idx` 恢复出发日。
- [x] **2026-09-27 22:00 · 天气能力（复用高德 key）**——`engine/travel_planner/weather.py` + CLI `weather` 命令。  
  项目有崖口东堤日落、稻田这类**强天气依赖项**却零天气能力。实测高德 `/v3/weather/weatherInfo`  
  用现有 key 直接可用（实况 + **4 天**预报）。踩到两个同名陷阱：中文简称「中山」会被静默返回  
  **大连市中山区**（与 geocode 同一个坑），须写全「中山市」或给 adcode。定位为**临行前能力**：  
  规划期只做气候参考，`checklist()` 给的是「出发前 3 天再查」的日期，不是数字。
- [x] **2026-09-27 22:00 · 同类项目对标与差距评估**——新增 `references/landscape.md`。  
  对 GitHub 上 6 个同类项目（travel-guidebook 89★、china-travel-kit 196★、skills-travel-planner、  
  citation-check-skill 249★、xiaohongshu-mcp 16k★、travel-itinerary-planner-agent-skill）逐条比对，  
  产出「该补什么 / **明确不补什么及理由**」——后者同样重要，避免被别人的功能列表牵着走。
- [x] **2026-09-27 22:40 · 源核验（把「标注」升级为「证据」）**——新增 `tools/source_audit.py`  
  \+ `references/source-audit.md`，成为**第五道硬闸门**。此前 `[A]` 写上去只要一个方括号，  
  它声称的「有来源、有时戳、可追溯」**没有任何机器验证过**——标注是声明，核验才是证据。  
  源核验 8 条规则：E1 徽章在册 / E2 `[A]` 时戳标注 / E3 `[B]` 双源 / E4 死线 /  
  E5 `[D]` 出路 / E6 source 闭环 / E7 同名数字一致 / E8 书写规范。  
  **首次跑真实产物就抓出 5 条真缺陷**（中山）：门票价标 `[C·单源]` 两处（死线违规）、  
  预算表「故居免费」标 `[C]`、送礼段 `[A]` 缺时戳、`weather-zs` 孤儿来源——全部为真，  
  已修（门票降 `[D]` 并补官方渠道出路、送礼段补时戳、孤儿来源挂到日落活动）。  
  **注意：这 5 处都不是数字错，是把单源信息标成了够不着的等级**——正是「标注纪律」与  
  「事实正确」的区别。负向样本 6 类违规全部命中；退出码 0/2 三态验证正确。
- [x] **C 级 · 工程保障（2026-09-28 落地）**——自动化测试套件 `tests/`（112 条测试，标准库  
  unittest）、`tools/ship.py` 一键回归（九道闸门 + 渲染基线，FAIL 退出码 2）、  
  CI `.github/workflows/gates.yml`（gates 跑 `ship.py` + `gitleaks` 密钥扫描）。  
  命令：`python verified-travel-planner/tools/ship.py`
- [ ] **C 级 · `examples/` 更多样例**——现有两个示例已分别覆盖「完整交付链路」（东莞）与  
  「渲染基线」（成都）两类用途；缺的是第三个用途（如**多城串联**或**纯过境**），需要时再补
- [x] **P4 · 行前公告扫描 `bulletin.py`（2026-10-01 落地）**——`--plan` 按 POI × 五类
  事件（闭馆/调价/管制/新开/活动）出 sweep 计划；`--check` 校验采集条目（时戳必填、
  **公告类死线只认 [A]**，[C] 传闻 error 退回）并产出渲染摘要；渲染走 #overview 注入
  （meta.bulletin，data-bulletin 可 grep），闭馆条目带「核对备选」提示——提示不改行程。
- [x] **P5 · 口碑体检 `review_trust.py`（2026-10-01 落地）**——跨批水军/推广**信号**：
  复读指纹（4-gram ≥3 条）/推广标记/立场-文本错配/同日爆发，并复用 social_notes
  单批信号；红线：只出信号不结论、不删评论、不产「调整后评分」；LLM 变体好评
  如实声明为盲区。渲染经 meta.social_intel.review_trust 进 #overview 条。
  配套 `references/review-connectors.md`（本地爬虫接入规范，stdlib 不内置爬虫）。
- [x] **P1 · 来源留痕与内容级复核（2026-09-30 v1 落地）**：采集侧——高德客户端每次成功调用
  都进 `call_log`（key 脱敏），`amap-snapshot` 默认内嵌 `raw_calls`（`--no-keep-raw` 可弃），
  `search-places`/`nearby-spots` 可 `--trace` 落盘；比对侧——新闸门 `claim_audit.py`
  把实采值（itinerary 段时长）拿回事实源全文里找，**实采 16 写成 15 这类数字幻觉 FAIL**。
  真实数据实测：东莞 6/6、上海 4/4 段时长全部可寻，零误伤；负向样本（改数）必红。
  **v2（2026-10-01）**：C1 硬面扩到实采段车费（estimated_cost），C2 池补单位换算
  容忍（米↔公里、分钟↔小时），东莞 UNVERIFIED 从 103 降到 96；快照未随附的
  距离/票价类仍如实列 UNVERIFIED——随附快照后即可回查（ship 会传 --snapshot）。
  **「采集与世界一致」由同日新增的第 2/3 层接力**（见下条）。
- [x] **P3 · 内容真实三层链（2026-10-01 落地）**：第 2 层 `echo_audit.py`——抓取
  线索卡引用页正文，验证声称的数字/时刻/关键短语在页面上（中文数字双向变体都认），
  死线类对不上即 FAIL，UNREACHABLE/断网只 WARN 不判死；第 3 层 `cross_check.py`——
  把各来源页面正文做 4-gram shingle 重合比对（阈值 0.7），「互相转载充双源」FAIL，
  同地不同数 DISPUTED 并列不裁决。echo/cross 两道新闸门已入 ship.py 名册（GATES 共 9）与 CI。
  设计依据与外部调研（ReviewMeta/Yelp 水军信号、SAFE/FActScore 事实核查管线、
  MinHash 转引检测）见 `references/echo-cross.md`。
- [x] **P2 · 上游归属声明的机器核验（2026-09-28 已落地）**：`THIRD_PARTY_NOTICES.md` 的
  「归属核对表」现列 **17 个**上游衍生文件（**8 个原样 / 9 个已改动**，2026-09-30 起
  `workflow.py` 因来源留痕从原样升为已改动）的 md5 与字节数，
  并新增三条自指规则由 `validate_skill.py` 校验：
  ① 台账里标「原样」的行数；② 台账 md5/字节与磁盘不符的行数（+文件缺失）；③ `LICENSE`
  归属标注与台账不一致的处数（**双向比对**：一边列了另一边没列也算）。后两条声明值恒为 0，
  改过表中任何文件却忘了更新即 **FAIL**。
  **来历**：2026-09-28 查「相比上游增加了什么」时发现，声明为「原样保留/迁移」的文件里
  **有 4 个其实被改过**（`amap.py` +221 行、`diagnostics.py` 241 行、`feasibility.py` +43 行、
  `路书_基准骨架.html` 的 CSS 改动还让版式指纹从 `9c3c6249a4` 变成了 `cd440ea068`），
  而当时 `validate_skill` 45/45 全绿**没抓到**——自指校验压根不覆盖这一类声明。
  更值得记的是：修完 `THIRD_PARTY_NOTICES.md` 后，**`LICENSE` 里同一处漂移还在**
  （4 个文件仍写着「原样」）。**一处声明常有两个副本**，所以规则 ③ 做成双向比对。

> ⚠️ **判断做没做，一律以文件系统为准，别信勾选框**——2026-09-27 就曾把一批**根本没建成**的文件  
> （`browser-use.md`、`client-compatibility.md`、`validate_skill.py` 等）误标成 `[x]`，  
> 是第二轮 `ls` 才裁决出来的。**自己写的勾也会错。**  
> 现在还多一条同源教训：**报错文案里的「运行 X」也要 `ls` 过再写**——  
> `doctor` 曾让用户去运行一个本包不存在的 `setup_rail_mcp.sh`。

> 体检 **8 项核心全 `READY`**（`ok: true`）；铁路 MCP 一项已定性为「不接入」（非缺陷），  
> 且不在 `doctor` 的检查项里——浏览器一项已由 `tools/shoot.py` + `references/browser-use.md`  
> 落地，**核心链路不受影响**。（2026-09-28 更正：原句引用的 `overall: PARTIAL` 口径  
> 已随 `doctor.py` 改版取消，见本文件上方同期更正。）

---

## 接着做

> 2026-09-27 13:22 更新：下面第 1、2 条本次会话已完成，真正待办从第 3 条开始。

1. ~~读上下文~~ ✅ 已完成：新会话以 `D:\旅游` 为工作区打开即自动加载 `.workbuddy\memory\`，无需手动复述。
2. ~~验证项目还活着~~ ✅ 已完成：4 项 `READY`，渲染与版式一致性校验全绿（见上表）。
3. ~~用一个真实目的地跑一次完整流程~~ ✅ **2026-09-27 已完成**：中山 3 天 2 晚，  
   六道闸门全绿，7 条实战教训已写入 `verified-travel-planner\references\experience.md` 的「本机记录」区。
4. **2026-09-27 22:00 又收口三项**：时效体检（`freshness.py`）、天气（CLI `weather`）、  
   同类项目对标（`references/landscape.md`）。跨设备可复现条件与打包要求见  
   `verified-travel-planner\references\portability.md`——**核心闸门零第三方依赖**，  
   换台机器 clone 即跑，只有自己的高德 key 与登录态 profile 要自建。
5. **2026-09-27 22:40 源核验收口**（`source_audit.py` 成为第五道闸门）：把「标注」升级为  
   「证据」——首次跑真实产物抓出 5 条真缺陷并全部修复。**下一层「来源留痕」见待办 P1**。
6. **2026-09-27 23:30 精简版重做**（`compact_check.py` 成为**第六道闸门**）：用户反馈  
   「精简版还是过多」，查根因是双档机制**装错了地方**——只做在占 35% 的 `sections` 上，  
   占 65% 的 `days[].slots` 没有双档能力；且把资料区收进 `<details>` 只移出首屏、  
   **字数一分未减**（伪精简），还与速览表「本页没有的都在完整版」的承诺自相矛盾。  
   改后中山精简版降到 **55.3%（7782 字）**，规范见 `references/dual-version.md`。
7. 之后按优先级选做：**C 级工程保障**（测试套件 / CI + gitleaks / 更多目的地示例），  
   以及 `references/landscape.md` 末尾的「下一步候选」表。  
   B 级扩能力五项**已全部收口**：`browser-use.md` + `shoot.py`、`client-compatibility.md`、  
   `validate_skill.py`、12306 铁路（定性为不接入）、社媒情报（`social-intake.md` + `social_notes.py`
   - `social-sources.md` + `social_source.py` + `social-login.md` + `social_login.py`：  
     三条采集路径、三份复用资产、21 条渠道五档矩阵，以及用户授权登录的单页只读读取）。

---

## ✅ 唯一真身与多端入口（已配好）

**`D:\旅游\` 是唯一真身。** 其他位置都是指向它的目录联接——改 D 盘，所有入口  
立刻生效，不存在"忘了同步"这种情况。

| 入口                                            | 谁在用                                                       |
| --------------------------------------------- | --------------------------------------------------------- |
| `~\.workbuddy\skills\verified-travel-planner` | WorkBuddy                                                 |
| `~\.workbuddy\skills\skill-doc-code-audit`    | WorkBuddy                                                 |
| `~\.agents\skills\verified-travel-planner`    | ZCode / Codex / Cursor / Copilot / Gemini CLI 等（跨客户端通用约定） |
| `~\.agents\skills\skill-doc-code-audit`       | 同上                                                        |

（`~` = 用户主目录；本机配置时间 2026-09-27。）

**已验证**：WorkBuddy 的清单缓存 `~\.workbuddy\.skill-list-cache.json` 显示  
`signature.missing = 0`，两个技能的 `description` 与 `filePath` 均正常读到  
——说明应用侧确实认下了这两个联接目录。

**ZCode 同样确认可用**（2026-09-27 从它打包的代码里实测）：ZCode 扫描 `~\.zcode\skills`  
与 `~\.agents\skills` 两处（同名时 `.zcode` 优先，并自动去重），所以上面那两个联接它  
直接就能读到。打开 ZCode 的「设置 → 技能」点右上角刷新即可看到。

- **删除联接不会伤到真身**（已实测）：`rmdir` 只删链接本身，本项目文件一个不少。
- 原目录已备份到 `~\.workbuddy\.skills-backup-20260927\`，用一段时间确认无误后可删。
- 以后想再加客户端（比如装了 Claude Code）：在它的 skills 目录里照建一个联接即可。  
  完整做法见技能 `project-migrate-resume` 的 `references/multi-client-linking.md`。

---

## 在其他 AI 工具里打开（ZCode / Codex / Cursor 等）

以 `D:\旅游` 为**工作区**打开即可。技能经上面的联接自动加载，无需额外配置。

**让 AI 读到项目背景** —— 工作区根目录已备好 `AGENTS.md`（2026-09-27 新增）：

| 客户端                      | 上下文来源                | 说明                          |
| ------------------------ | -------------------- | --------------------------- |
| WorkBuddy                | `.workbuddy\memory\` | 自动加载日志与长期记忆                 |
| ZCode / Codex / Cursor 等 | **`AGENTS.md`**      | 从工作目录**向上逐级查找**，打开哪个子目录都能找到 |

两条通道互不干扰，指向同一份项目事实。`AGENTS.md` 里写了：项目定位、三条铁律、  
目录地图、运行命令、5 条高频陷阱、环境约束、技能生效方式、当前状态与待办。

**ZCode 实测事实**（从它打包的代码 `resources\glm\zcode.cjs` 里核出，非查文档）：

| 项      | 结论                                                                                                                  |
| ------ | ------------------------------------------------------------------------------------------------------------------- |
| 技能根    | 用户级 `~\.zcode\skills` + `~\.agents\skills`；项目级 `<工作区>\.zcode\skills` + `<工作区>\.agents\skills`（**项目级优先级更高**，且跟随符号链接） |
| 上下文文件  | `AGENTS.md`；用户级后备 `~\.zcode\AGENTS.md`                                                                              |
| MCP 配置 | **工作区根 `.mcp.json`**（与 Claude Code 同族）；内置 `/mcp`、`/init` 命令                                                         |
| 格式限制   | `description` ≤ 1024 字符（**超出该技能被整条丢弃**，不是截断）；正文 ≤ 100 KB（超出截断）                                                      |
| 本项目余量  | 不写死在这里 —— 见 `references/client-compatibility.md`，那里是这些数字的唯一出处                                                       |

打开 ZCode →「设置 → 技能」→ 右上角刷新，应能看到这两个技能。

**已补（2026-09-27）**：`doctor --client` 现已支持 `zcode` 与 `workbuddy` 两个分支，  
不再只有 `codex` / `claude-code`。各客户端的 MCP 配置位置、格式限制与当前余量，  
统一写在 `references/client-compatibility.md`。

---

## 分享给别人

`D:\旅游\verified-travel-planner\` 本身就是一个可独立打包的目录。

| 方式        | 做法                                      |
| --------- | --------------------------------------- |
| **zip**   | 打包该目录发给对方 → 解压进他自己客户端的 skills 目录        |
| **git**   | 推成仓库 → 对方执行 `npx skills add <你>/<仓库>`   |
| **通用加载器** | 对方执行 `npx openskills install`（跨客户端一次装齐） |

⚠️ 分发前必查三条：

1. `LICENSE` + `THIRD_PARTY_NOTICES.md` **必须随包**（MIT 衍生作品的版权义务）。
2. 必须是**真实副本**——目录联接不能直接打包分发。
3. **"装得上" ≠ "跑得起来"**：本 skill 靠 Python 脚本干活，对方客户端是否放行  
   Bash/Python 执行**尚未实测**。正式对外发布前，请先在目标客户端试跑一次。

> 📦 **跨设备能不能接着跑？** 能。核心闸门（渲染 / 自查 / 可行性 / 一致性）**零第三方依赖**，  
> 34 个标准库模块，对方 clone 下来即可执行；只有**高德 key** 与**登录态 profile** 属于  
> 「机器级私有状态」，必须各自自建（两者都在 `~/.verified-travel-planner/`，已 `.gitignore` 隔离）。  
> 平台绑定项（无头浏览器探针）在 Windows 已实测，非 Windows 标「未实测」而非「不可用」。  
> 完整条件与打包要求见 `verified-travel-planner/references/portability.md`；  
> 发布前跑**一条**就够：`python verified-travel-planner/tools/ship.py`  
> （九道闸门 + 渲染基线一键回归，FAIL 退出码 2；`doctor.py` 与 `validate_skill.py`  
> 都在它里面）。

> ⚠️ **`.gitattributes` 不能少**（`* -text`）：Windows 默认把 LF 转 CRLF，  
> 那会让**逐字节可重现**的回归基线永久失败。缺 `assets/` 同理——渲染器会直接拒渲染。

---

## 合规提示

本项目是 MIT 许可作品的衍生作品，`LICENSE` 与 `THIRD_PARTY_NOTICES.md`  
必须随源码保留。二次分发时不要把 `LICENSE` 删掉。

仓库根目录不再重复放一份 `LICENSE`——那里放的是一份**指路牌**，说明  
「产品是 `verified-travel-planner/`，其余的过程材料不属于产品」。  
背景见根目录 `LICENSE`；操作清单见本地 `验收记录/GitHub发布准备_2026-09-28.md`（私有，不入库）。

---

## 发布版本

| 版本         | 日期         | 状态     | 依据                          |
| ---------- | ---------- | ------ | --------------------------- |
| **v1.0.0** | 2026-09-28 | 首个发布版本 | 发布前就绪度审查：阻断项 0（审查报告为本地私有记录） |

发布前自检（每次改动后都跑，不要跳）：

```bash
python verified-travel-planner/tools/ship.py          # 全量：九道闸门 + 渲染基线
python verified-travel-planner/tools/ship.py --quick  # 只改文档时用
```

版本化约定：**打标签即发布**（`git tag -a v1.0.0`）。改动了事实源 / 渲染器 / 闸门后，  
发布前必须确认 `ship.py` 全绿——尤其是**渲染复现**那一条（由事实源重渲染与既有 HTML  
逐字节比对），它会先于任何人发现「产物与源不一致」。
