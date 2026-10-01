# 跨设备可复现：哪些依赖本机，哪些不依赖

> 一句话结论：**核心流程零依赖、换台机器就能跑；只有「登录态」与「高德 key」两样
> 是本机私有状态，别人必须自建。**
>
> 机器可查：`python <SKILL_ROOT>/tools/doctor.py`（第 6/7/8 项就是为此加的）。

---

## 一、先把依赖分三类，别混为一谈

回答「换个设备还能不能跑」，必须先分清**问的是哪一层**。三层混着说，
就会得出「这项目是不是绑死在 Windows 上」这种没法回答的问题。

| 层 | 依赖什么 | 换设备的后果 | 判据 |
|---|---|---|---|
| **A 层 · 核心闸门** | 只依赖 Python 标准库 | **零影响**，clone 下来直接跑 | `doctor` 第 6 项「第三方依赖」 |
| **B 层 · 平台绑定项** | 操作系统 / 浏览器 / 网络出口 | Windows 实测可用；非 Windows **未实测** | `doctor` 第 7 项「平台绑定项」 |
| **C 层 · 机器级私有状态** | 你自己的账号与凭据 | **必须自建**（这是设计，不是缺陷） | `doctor` 第 8 项「机器级私有状态」 |

> **为什么这个分法重要**：很多人担心的「依赖本机环境」其实是 C 层——
> 而 C 层**本来就不该入库**（登录态进了仓库等于把账号送人）。
> 真正决定「别人能不能跑」的是 A 层，而 A 层是零依赖。

---

## 二、A 层：核心闸门 —— 完全不依赖

实测结果（`doctor` 输出）：**零第三方依赖，34 个标准库模块**。

具体是这几件事，全部只 import 标准库：

| 能力 | 工具 | 依赖 |
|---|---|---|
| 路书渲染 | `tools/render_html.py` | `json` / `re` / `base64` / `mimetypes` |
| 版式一致性 | `tools/consistency.py` | `re` / `hashlib` |
| 30 项交付自查 | `tools/ludbook_check.py` | `re` / `json` |
| 可行性引擎 | `engine/travel_planner/`（14 模块） | `datetime` / `zoneinfo` / `urllib` |
| 社媒线索归一化 | `tools/social_notes.py` | `json` / `re` |
| 渠道矩阵 / 采集计划 | `tools/social_source.py` | `json` / `socket` |
| 环境体检 | `tools/doctor.py` | `ast` / `importlib` / `sysconfig` |
| 截图核验 | `tools/shoot.py` | `subprocess`（调系统浏览器） |
| 脱敏 | `tools/desource.py` | `re` |

**唯一两个真实前置条件**：

1. **Python ≥ 3.9**（低于 3.9 无 `zoneinfo`，可行性引擎要解析真实时区）；
2. **Windows 上必须装 `tzdata`**——Python 在 Windows 不带时区库，缺它跑 `doctor`
   会报 `ZoneInfoNotFoundError: Asia/Shanghai`。装它用阿里源：
   ```bash
   pip install tzdata -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
   ```
   （清华源对该包返回 `No matching distribution found`，实测过，别浪费时间。）

> 顺带说明一个**容易误判成项目问题**的本机现象：本机 Git Bash 的 PATH 是坏的
> （`ls`/`cat` 都 command not found）。那是**本机沙箱环境**的问题，
> **不是这个 skill 的问题**，别人不会遇到。

### 工程陷阱：CLI 模块与引擎包同名遮蔽（2026-09-28 实测踩过）

`tools/travel_planner.py`（CLI **模块**）与 `engine/travel_planner/`（检查引擎**包**）同名。
写测试或新工具时若把两个目录都塞进 `sys.path`，`import travel_planner` 命中谁取决于
path 顺序，且报错极其难懂（`ModuleNotFoundError: No module named 'travel_planner.amap';
'travel_planner' is not a package`——把 CLI 模块当包拆了）。

**正确做法**（`tests/test_gate_inputs.py` 的现成范例）：

- 引擎走 `sys.path`：`sys.path.insert(0, <SKILL_ROOT>/engine)` 后
  `from travel_planner.research import validate_plan_content` 正常；
- `tools/` 下的模块**不进 sys.path**，用 `importlib.util.spec_from_file_location`
  按文件路径加载。

新加任何要同时 import 两边的代码，先照这个模式写，别凭直觉两个 path 都插。

---

## 三、B 层：平台绑定项 —— 边界要说清，别含糊

| 能力 | 绑定什么 | Windows（本机已实测） | 其他平台 |
|---|---|---|---|
| 全部核心闸门 | 无 | ✅ 可用 | ✅ 可用（同为零依赖） |
| `tools/social_login.py --check/--launch/--grab` | Edge/Chrome 可执行文件 | ✅ 实测可用 | ⚠ **未实测** |
| `tools/shoot.py` 截图 | 系统 Edge/Chrome | ✅ 实测可用 | ⚠ 未实测 |
| 无头浏览器取 DOM | 同上 | ✅ | ⚠ 未实测 |
| 高德采集 | 网络出口 | ✅ | 视网络 |

**必须如实交代的三处 Windows 绑定**（都在 `social_login.py` 里，不藏着）：

1. 浏览器候选路径写的是 `C:\Program Files (x86)\Microsoft\Edge\...` 等 Windows 路径
   ——**有 `shutil.which('msedge'/'chrome'/'chromium')` 兜底**，Linux/macOS 上通常能找到；
2. 浏览器版本探测调 `powershell -NoProfile -Command "(Get-Item ...).VersionInfo.ProductVersion"`
   ——非 Windows 上会抛异常，被 `except` 兜住并返回「未知」（**功能不中断，只是少一行信息**）；
3. 独立 profile 的占用探测在 Windows 走 `Get-CimInstance Win32_Process`，
   非 Windows 上先判 `SingletonLock` 文件（POSIX 的 Chromium 系会建它）。

> **口径是「未实测」而不是「不可用」**——因为没在 Linux/macOS 上真跑过。
> 按铁律 1，没核实的不许写成已核实。要用 AUTH 档，先跑一次 `--check` 看实际结果。

**另一条与地域有关、与设备无关的事实**：小红书 / 抖音 / 知乎 / 马蜂窝在本机网络
（未登录、无 Cookie）**全部被风控拦截**。这**不是本机独有**——是这些站点对
「无头浏览器 + 数据中心/家宽出口」的普遍姿态。换台机器**不保证能过**。
所以 `social-sources.md` 里那 21 条渠道的可用状态，**换设备后要重跑 `--doctor`**。

---

## 四、C 层：机器级私有状态 —— 必须自建，且绝不入库

| 状态 | 位置 | 入库？ | 别人怎么办 |
|---|---|---|---|
| 高德地图 key | `~/.verified-travel-planner/credentials.json` | ❌ **绝不** | 跑 `tools/set_amap_key.py` 配自己的 key |
| 登录态 profile（AUTH 档） | `~/.verified-travel-planner/social-profile/` | ❌ **绝不** | 跑 `social_login.py --launch` **自己扫码** |
| MCP 配置 | `~/.workbuddy/mcp.json` | ❌ 机器级 | 按需自建 |

`.gitignore` 已经把这三类排除掉了（`.verified-travel-planner/`、`credentials.json`、`*.key`），
所以**不存在"不小心把凭据推上去"的路径**。

**登录态为什么必须自建**，一句话：**我不该也不能替任何人登录**。所以
「AUTH 档的产出无法跨设备复现」是**设计上的明确取舍**，不是没做。

> 还有一条必须说清的：**没有高德 key 也照样能跑完整流程**——只是
> POI 与里程/车程会按铁律 1 降级成 `[D]`，并在路书里如实标出。
> 「能力降级已声明」是 30 项自查的第 5 项。

---

## 五、发布到 GitHub：别人下载后能做什么

| 别人想做的事 | 能不能直接做 | 还差什么 |
|---|---|---|
| 看示例路书（`产出示例/路书_成都_渲染示例.html`） | ✅ 直接双击 | 无（单文件自包含、断网可开） |
| 跑回归（重渲染应与示例逐字节一致） | ✅ 直接跑 | 无（零依赖） |
| 体检自己的环境 | ✅ `python tools/doctor.py` | 无 |
| 校验包完整性（19 条规则 / 79 处断言） | ✅ `python tools/validate_skill.py` | 无 |
| 生成一份**新目的地**的路书 | ⚠ 骨架与闸门都能跑 | **自己的高德 key**（否则 POI 全 `[D]`） |
| 采社媒情报（OPEN 档三条路径） | ⚠ 视网络 | 网络可达性，站点风控与出口 IP 有关 |
| 用登录态读小红书/抖音（AUTH 档） | ⚠ 技术上可以 | **自己扫码**，且账号风险自负 |
| 复现**中山那份**社媒线索卡 | ❌ 不必也不该 | 线索卡本身已随示例入库；重采结果必然不同（时效） |

**一句话**：**核心自动化 100% 可跨设备复现；只有「登录态」和「高德 key」两样要自建。**

---

## 六、打包要求（要发布就必须满足）

### 必须带

| 文件 / 目录 | 为什么不能少 |
|---|---|
| `assets/路书_基准骨架.html` | 渲染器的真值来源。**缺了直接拒渲染**（不是降级，是报错退出） |
| `assets/路书事实源模板.json` | 事实源的起点，缺了没法开工 |
| `assets/情报卡_基准骨架.html` | 同理 |
| `.gitattributes`（内容 `* -text`） | **别删**。它禁止换行符自动转换——Windows 默认 `core.autocrlf=true` 会把 LF 换成 CRLF，**逐字节回归基线会永久失败** |
| `tools/` 全部 + `engine/` 全部 | 闸门与引擎 |
| `references/` 全部 | 契约文档与经验沉淀 |
| `LICENSE` + `THIRD_PARTY_NOTICES.md` | MIT 合规（含两个上游的完整版权声明），**勿删** |
| `产出示例/` | 回归基线。**没有它，「逐字节一致」这句承诺就没法被验证** |

### 不要带

| 排除项 | 原因 | 靠什么排除 |
|---|---|---|
| `~/.verified-travel-planner/` | 登录态 + 凭据，**入仓等于送号** | `.gitignore` |
| `产出示例/**/发布/` | 是源文件的副本，可由源重新生成；入库会与源不一致 | `.gitignore` |
| `__pycache__/`、`*.pyc` | 编译产物 | `.gitignore` |
| `.wbapp_*.genie` | WorkBuddy 内部标记文件 | `.gitignore` |

### 不需要带

**没有 `requirements.txt`，也不需要**——零第三方依赖本身就是这个项目的特点。
真要说前置条件，只有两条：**Python ≥ 3.9** + **Windows 装 `tzdata`**。

### 发布前自检（两条命令）

```bash
python tools/doctor.py          # 8 项：应全 ✅ / ℹ，无 ❌
python tools/validate_skill.py  # 19 条规则 / 79 处断言：应全绿
```

> **建议把这两条写进 CI**（`P3` 待办里已有「CI + gitleaks」）。
> `doctor` 保证「别人的机器跑得起来」，`validate_skill` 保证「文档没撒谎」，
> `gitleaks` 保证「凭据没被推上去」——三件事互不重叠，正好凑成发布前的三道门。

---

## 七、已知不可复现项（如实列，不糊过去）

1. **AUTH 档产出**——依赖你的账号登录态，他人无法复现（也不该复现）；
2. **社媒渠道的可用性矩阵**——站点风控随时间与出口 IP 变化，**换设备 / 隔几天都要重测**；
3. **高德 key 相关的 `[A]` 级 POI 与车程**——没配 key 的人只能拿到 `[D]`；
4. **`github.com` 网页直连**——本机实测超时（`api` / `raw` / `codeload` 三域可达，
   所以**能装 tarball、不能 `git clone`**）。这是**本机网络**问题，别人未必遇到。
5. **无头浏览器最小视口约 496px**——真机 375/390px 截图 `shoot.py` 截不了
   （会原地裁剪而不是缩放），第 ⑳ 项的手机截图须真机补。
