# AGENTS.md · 旅游规划器（可核验版）

> 给 AI 编码助手（ZCode / Codex / Cursor 等）读的**上下文入口**。
> 人类读者看 `README.md`（对外版，GitHub 首页）；工作区说明在 `WORKSPACE.md`；WorkBuddy 的上下文在 `.workbuddy/memory/`（本文件已摘要其要点）。
> **⚠️ 仓储边界**：`.workbuddy/` 与 `验收记录/` 是**本地私有**、已被 `.gitignore` 排除、
> 也**不随公开仓分发**（它们含本机路径与上线链接）。公开仓里只有
> `verified-travel-planner/`（产品）、`产出示例/`（示例）、`skill-doc-code-audit/` 与说明文件。
> 因此本文件里凡提到「`.workbuddy/memory/` 会被自动加载」都是指**本机工作区**的行为。

---

## 这是什么

一个**旅行规划 Agent Skill**，融合两个开源项目的长处：

| 来源 | 负责什么 |
| --- | --- |
| 见好旅行规划器（jianhao，MIT） | 交付得像样——30 项交付自查、六维度玩法、三池归属、双写制、脱敏、去 AI 味 |
| tanweiping1012 的实证派做法 | 数字不骗人——证据四级分级、时戳纪律、能力先测后报、只读边界 |

核心主张：**每个数字都有来源，每处结论都能被机器校验。**

**三条铁律**（改代码时不得破坏）：

1. **证据分级**：`[A]` 工具实证 → `[B]` 多源交叉 → `[C]` 单源线索 → `[D]` 未核实。
   无地图能力时，POI 与距离/时长**一律标 `[D]`**，不得用模型推断的坐标冒充已核实。
2. **排不通不给**：`evaluate` 做确定性可行性检查（时间窗 / 换乘余量 / 营业时间 / 预算闭合），
   `INFEASIBLE` 必须**退出码 2**，不交付。
3. **只读边界**：不购买、不预订、不支付、不候补、不改签；不输入密码 / 验证码 / 身份证 / 支付信息。

---

## 目录地图

```
D:\旅游\                          ← 唯一真身（各客户端经目录联接加载）
├── AGENTS.md                     ← 本文件（AI 上下文入口）
├── README.md                     ← 对外项目说明（GitHub 首页）
├── WORKSPACE.md                  ← 工作区说明（公开范围表 + 待办，2026-09-30 由 README.md 改名）
├── .workbuddy\memory\            ← 🔒 私有，不入库：2026-09-27.md（开发日志）+ MEMORY.md
├── verified-travel-planner\      ← ★ 项目主体
│   ├── SKILL.md                  ← 主工作流：铁律、阶段 0-6、阶段 4.5 随行套件
│   ├── LICENSE                   ← 上游 MIT 版权声明（合规必需，勿删）
│   ├── THIRD_PARTY_NOTICES.md    ← 两个上游的完整版权声明
│   ├── engine\travel_planner\    ← 检查引擎（14 模块，纯标准库）
│   ├── tools\                    ← 命令行工具（见下）
│   ├── references\               ← 渐进加载参考文档（用到才读）
│   └── assets\                   ← 基准骨架 + 事实源模板
├── skill-doc-code-audit\         ← 配套工具：审计「文档声称的」与「代码实现的」是否一致
├── 产出示例\                     ← 东莞（完整交付链路示例）＋ 成都（渲染回归基线桩件）
└── 验收记录\                     ← 🔒 私有，不入库：高德采集入参出参 + 迁移/联接脚本 + 验收报告
```

`tools/` 关键文件：`travel_planner.py`（★统一 CLI，14 命令）、`render_html.py`（事实源 JSON → 单文件 HTML，
加 `--compact` 出精简执行版）、`consistency.py`（版式校验）、`ludbook_check.py`（30 项交付自查）、
`freshness.py`（时效体检：动态数字到出发日还新不新）、`source_audit.py`（源核验：徽章标注纪律闸门，源核验 8 条规则）、`claim_audit.py`（声明↔留痕比对闸门：实采段时长与车费必须如实进路书，v2）、`echo_audit.py`（来源回声核查闸门：抓引用页验证声称值在页面上，内容真实第 2 层）、`cross_check.py`（独立源核查闸门：正文重合拆穿转引充双源，第 3 层）、`compact_draft.py`（compact 半自动草稿：只出草案副产物永不写事实源，约束段不进删除通道）、`social_notes.py`（社会情报归一化 + 增量合并 + 交叉印证）、
`social_source.py`（社媒采集渠道能力矩阵 + 采集计划）、`social_login.py`（AUTH 档登录态探针）、
`cdp_read.py`（CDP 只读读取器，穿透 Shadow DOM 读评论）、`bulletin.py`（行前公告扫描：事件 sweep 计划 + 死线分级 + 渲染摘要）、`review_trust.py`（口碑体检：水军/推广信号，只出信号不结论）、
`validate_skill.py`、`shoot.py`、`set_amap_key.py`、`doctor.py`、`desource.py`、
`ship.py`（★一键回归：九道闸门 + 渲染基线跑一遍，FAIL 退出码 2）。

**发布前 / 大改后跑这一条就够**（不用再手敲七条命令）：
`python verified-travel-planner/tools/ship.py`（加 `--quick` 跳过渲染复现与体检，
`--only 城市` 只跑某个示例，`--list` 只看识别到哪些示例）。

---

## 怎么跑

**Python**：任意 Python 3.9+（本机实测 3.13.12）
（引擎纯标准库；Windows 另需 `tzdata`，见下）

```bash
cd verified-travel-planner

# 全部 14 个命令
python tools/travel_planner.py --help

# 环境体检（客户端参数必带——不带会自己猜，装了几个客户端时猜不准）
python tools/travel_planner.py doctor --client workbuddy

# 查 POI（需高德 key）
python tools/travel_planner.py search-places --keywords "宽窄巷子" --city 成都

# 采集坐标+路线+周边（需 key；起终点必须是【城市级】）
python tools/travel_planner.py amap-snapshot --input 输入.json --output 快照.json

# 可行性检查（排不通 → 退出码 2）
python tools/travel_planner.py evaluate --input 行程.json

# 渲染路书（数据与模板分离）
python tools/render_html.py 事实源.json -o 路书.html

# 版式一致性校验
python tools/consistency.py 路书.html --base assets/路书_基准骨架.html

# 时效体检（动态数字到出发日还新不新；--strict 有须重查项则退出码 2）
python tools/freshness.py 事实源.json --on 2026-09-28

# 源核验（徽章标注纪律；有 FAIL 退出码 2。成都是 legacy 版式基线，E1 对它必然红，见 source-audit.md）
python tools/source_audit.py --facts 事实源.json --plan final_plan.json

# 天气（需 key；预报窗口仅 4 天，属临行前能力；城市名要写全或直接给 adcode）
python tools/travel_planner.py weather --city 中山市 --start 2026-09-28 --end 2026-09-30
```

回归基线：改完代码跑 `doctor` + 用 `产出示例\路书_成都_事实源.json` 重渲染，结果应与
`产出示例\路书_成都_渲染示例.html` **逐字节相同**。
（2026-09-30 实测：102836 字节，整文件 MD5 `df75e371260c251ef5d6dd1c9f63cc86`，连 4 张配图的
base64 都逐字节一致。骨架每次 CSS 变更都会换基线——改骨架与重渲染三城示例必须同一提交。）

---

## 高频陷阱（都踩过，别再踩）

1. CLI 是子命令 `credential-status`，**不是** `--status`。
2. `doctor` 不带 `--client` 会自己猜客户端。可选值 `auto` / `workbuddy` / `zcode` /
   `codex` / `claude-code` / `generic`。装了多个客户端时「哪个在跑我」是读不出来的——
   报告里会带 `client_candidates`（全部痕迹）与 `client_ambiguous`（是否多于一），
   显式 `--client <名字>` 才是真实结论。各客户端的 MCP 配置位置见
   `references/client-compatibility.md`。
3. `amap-snapshot` 的 `origin`/`destination` 必须是**城市级**实体；传 POI（如「珠海站」）会被
   覆盖门禁**主动拒绝**——这是防「高德查境外地点返同名国内地点」的误匹配设计，不是 bug。
   城内具体景点坐标走 `search-places` 这条通道，两条别混用。
4. 渲染器要求事实源里的本地配图按**相对事实源所在目录**解析；缺图会明确报出路径并拒绝渲染（不静默）。
5. Windows 必须装 `tzdata`，**且必须用阿里源**——清华源对该包返回「查无此包」：
   `pip install tzdata -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com`
6. **本机有两个 Python**（2026-09-30 实测）：WorkBuddy 自带的 3.13（项目默认用它）与系统
   `AppData\Local\Programs\Python\Python312`。裸 shell 里 `python` 可能解析到后者——
   闸门照样全绿（核心闸门不依赖 tzdata），但 `doctor` 会报「时区数据库缺失」并被误读成
   环境坏了。**两个解释器现都已装 tzdata**（2026-09-30 修根，阿里源），doctor 双双全绿；
   doctor 的报错仍带出事解释器路径（放最前防截断），万一再现照着装即可。

---

## 环境约束

- **高德 key**：放在用户主目录的 `~/.verified-travel-planner/credentials.json`（本机已配置）。
  这是**机器级凭证，不随项目走**，勿拷进版本库。
  - ⚠️ 别设 `AMAP_API_KEY` 环境变量——它会遮蔽凭据文件。
  - ⚠️ 个人认证配额：关键字/周边搜索约 **100 次/日**；地理编码、路径规划 5000 次/日；每日 0 点重置。
- **Bash（Git Bash）的 PATH 是坏的**（本机现象），每条命令开头补：
  `export PATH="/usr/bin:/bin:<PortableGit 的 usr/bin>:/c/Windows/System32:/c/Windows:$PATH"`
- 凭证在 **Python 的 home** 下的 `.verified-travel-planner/`（即 `Path.home()`，本机为
  `C:\Users\<你的用户名>`），**不在** Git Bash 的 `~`（`HOME` 被改指到别处，
  别在那里找不到就误判「没配上」）。
- 本项目已用 git 管理（2026-09-28 起，含完整提交历史）。`.gitignore` 的公开范围约定见
  `WORKSPACE.md` 顶部那张表：`.workbuddy/` 与 `验收记录/` 已**取消跟踪并忽略**（本地文件保留），
  另忽略 `__pycache__`、发布产物、`*.bak-*` 与机器级私有状态。
  ⚠️ **取消跟踪不等于从历史里消失**——公开前仍须清理历史，见 `PUBLISHING.md`。ZCode 的「项目级技能根」仍只扫到工作区这一层。

---

## 技能如何生效（多端入口）

`D:\旅游\` 是唯一真身，各客户端通过**目录联接**加载，改 D 盘即时对全部入口生效：

| 入口 | 客户端 |
| --- | --- |
| `~\.workbuddy\skills\verified-travel-planner` 等 | WorkBuddy |
| `~\.agents\skills\verified-travel-planner` 等 | ZCode / Codex / Cursor / Copilot / Gemini CLI |

- ZCode 会扫 `~\.zcode\skills` 与 `~\.agents\skills`（同名时 `.zcode` 优先、自动去重），
  以及**工作区级** `<工作区>\.zcode\skills` 与 `<工作区>\.agents\skills`（项目级优先级更高）。
- 删除联接不会伤到真身；原目录已备份在 `~\.workbuddy\.skills-backup-20260927\`。
- 给别人用：`D:\旅游\verified-travel-planner\` 可独立打包（zip / git / `npx skills add`）。
  ⚠️ 分发前必查：`LICENSE` + `THIRD_PARTY_NOTICES.md` 必须随包；必须是真实副本（联接不能直接打包）；
  **「装得上」≠「跑得起来」**——对方客户端是否放行 Python 执行尚未实测。

---

## 当前状态与待办

- **A 级全落地**：双核融合、引擎 14 模块、统一 CLI 14 命令、渲染管线（配图 base64 内联、断网可开）、
  MIT 合规、契约文档 9 章。
- **高德 key 已通过 4 步验收**：`credential-status` CONFIGURED → `doctor --live` 真连成功 →
  `search-places` 返回真实 POI → `amap-snapshot` 采集成功（`provenance.live_data = true`）。
- 环境体检 **8 项**（Python 解释器 / 时区数据库 / 检查引擎〔以上 critical〕+ 高德地图 key /
  随包资产 / 第三方依赖 / 平台绑定项 / 机器级私有状态），核心全 `READY`，`ok: true`。
  命令：`python verified-travel-planner/tools/doctor.py`（加 `--json` 出机读结果）。
  铁路 MCP 一项 2026-09-27 已定性为「**不接入**」（可获取市场无 12306 官方 server，
  只有第三方 OTA／差旅聚合，且本包从未搬入上游的安装脚本），属**非缺陷**，不是待办；
  它也不在 `doctor` 的检查项里（旧文档里的 `overall: PARTIAL` 口径已随 `doctor.py`
  改版取消，见 `WORKSPACE.md` 同期更正）。
- **已建工具**：`tools/validate_skill.py`（文档计数断言自动校验，19 条规则覆盖 79 处断言）、
  `tools/source_audit.py`（源核验闸门：E1-E8 标注纪律，有 FAIL 退出码 2）、
  `tools/shoot.py`（无头浏览器溢出探针 + 截图）、
  `tools/social_source.py`（社媒采集渠道能力矩阵 21 条 + 采集计划生成）、
  `references/client-compatibility.md`（客户端兼容矩阵，证据分「实测 / 据实现」两级）、
  `references/browser-use.md`（浏览器核验能力与两个陷阱）、
  `references/social-sources.md`（社媒渠道五档矩阵 + 五条扩展路径 + 合规/反爬/解析）、
  `references/social-login.md`（用户授权登录：三种强度 / 实测 / 账号风险）。
- **工程保障已落地（2026-09-28）**：`tests/`（109 条测试，标准库 unittest）、
  `tools/ship.py` 一键回归（九道闸门 + 渲染基线，FAIL 退出码 2）、
  CI `.github/workflows/gates.yml`（gates + gitleaks）。
- **内容真实三层链（2026-10-01 全部落地）**：第 1 层 `claim_audit`（v2：实采段
  时长+车费硬查）；第 2 层 `echo_audit`（抓引用页验证声称值在页面上，死线类
  对不上即 FAIL，UNREACHABLE 只 WARN）；第 3 层 `cross_check`（来源页正文重合
  比对，拆穿转引充双源）。三层全过仍 ≠「数字一定对」，见 README〈已知边界〉1。
- **内容真实旁路（2026-10-01）**：`bulletin.py`（官方渠道变更公告：sweep 计划
  + 死线分级〔公告类只认 [A]〕+ meta.bulletin 首屏渲染，提示不改行程）；
  `review_trust.py`（评论区水军/推广信号：复读指纹/推广标记/立场错配/同日爆发，
  只出信号不结论、不产调整后评分；LLM 变体好评是如实声明的盲区）。
- **待办**：`examples/` 更多样例（现三个已覆盖「完整交付链路」「渲染基线」
  「路线/备选/顺道点」三类用途，缺如**多城串联 / 纯过境**）。

---

## 合规

本项目是 MIT 许可作品的**衍生作品**。`LICENSE` 与 `THIRD_PARTY_NOTICES.md`
必须随源码保留，二次分发时不要删。
