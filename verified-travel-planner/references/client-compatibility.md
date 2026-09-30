# 客户端兼容性

> 回答一个问题：**换一个 Agent 客户端，这套 skill 还跑得起来吗？**

先说结论：**技能本体（`engine/` + `tools/`）是纯 Python 标准库，零第三方依赖。**
只要客户端放行 Python 执行，核心链路就能跑。跨客户端的差异只有三处：
**技能从哪加载**、**上下文文件叫什么**、**MCP 配置写在哪**。

## 证据等级

沿用铁律 1 的四级，但这里口径收窄为「验证方式」：

| 级别 | 含义 |
|---|---|
| `[实测]` | 在**本机真查过**（读文件 / grep 客户端内核 / 真跑过命令），附日期与复现方法 |
| `[据实现]` | 从本项目代码的检测逻辑推出来的，**没在真机验过**。用之前自己确认一次 |
| `[未核实]` | 只知道有这回事。表里会给出验证方法，别当结论用 |

**注意**：上一节写的「技能根」这类路径，客户端版本一升级就可能变。
本文件标注的都是 **2026-09-27** 的状态。**过期了就以实测为准，改这份文件。**

---

## 兼容矩阵

| 客户端 | 技能根（用户级） | 技能根（项目级） | 上下文文件 | MCP 配置 | 证据 |
|---|---|---|---|---|---|
| **WorkBuddy** | `~/.workbuddy/skills/` | `<工作区>/.workbuddy/skills/` | `.workbuddy/memory/`、`AGENTS.md` | `~/.workbuddy/mcp.json` | `[实测]` 目录存在 + `[据实现]` 路径 |
| **ZCode**（智谱） | `~/.zcode/skills/`、`~/.agents/skills/` | `<工作区>/.zcode/skills/`、`<工作区>/.agents/skills/` | `AGENTS.md`（用户级后备 `~/.zcode/AGENTS.md`） | `<工作区>/.mcp.json` | `[实测]` 2026-09-27，见下 |
| **Claude Code** | `[未核实]` | `[未核实]` | `CLAUDE.md` `[未核实]` | `~/.claude.json` 的 `mcpServers` | `[据实现]` 检测代码读这个文件 |
| **Codex CLI** | `[未核实]` | `[未核实]` | `AGENTS.md` `[未核实]` | `codex mcp add/get` 命令 | `[据实现]` 检测代码用这条命令 |
| **无技能机制的宿主** | — | — | — | — | `[实测]` 直接跑 `tools/` 下的脚本即可 |

「项目级优先于用户级」是 ZCode 的实测结论；其他客户端**未核实**。

---

## ZCode（智谱）· 实测明细

2026-09-27 用 grep 客户端内核的方式实测（方法见文末）。ZCode 是 Electron 应用，
内核在 `resources/glm/zcode.cjs`。

**技能加载**

- 用户级：`~/.zcode/skills` + `~/.agents/skills`
- 项目级：`<工作区>/.zcode/skills` + `<工作区>/.agents/skills`（**优先级更高**）
- 同名技能自动去重，`.zcode` 优先于 `.agents`
- **跟随符号链接**（`followSymbolicLinks` 默认 true）——这一条很关键：
  本项目在多端之间用**目录联接**（Windows junction）共享同一份真身，
  ZCode 能把联接扫到。**换成不支持符号链接的客户端，多端方案就不成立。**

**上下文文件**：`AGENTS.md`。用户级后备 `~/.zcode/AGENTS.md`。

**MCP**：读**工作区根 `.mcp.json`**，格式与 Claude Code 同族（`mcpServers` 对象）。
内置 `/mcp`、`/init` 命令。项目级还有 `<工作区>/.zcode/mcp.json` 这个位置，
`doctor --client zcode` 会两个都查。

**格式限制（最容易踩的坑）**

| 项 | 上限 | 超出后果 |
|---|---|---|
| `description` | 1024 字符 | **整个技能被丢弃**——不是截断，是它压根不出现 |
| 正文 | 100 KB | 截断 |

**本项目当前余量**：

- `SKILL.md` 的 `description` **280 字符**（上限 1024，余量 744）
  → 这个数由 `tools/validate_skill.py` 校验：改描述忘了改这里，脚本会失败。
- `SKILL.md` 正文 **约 46 KB**（上限 100 KB）
  → 不写死精确值——每次编辑都会变。只保证不逼近上限。

→ 两条都安全，但**改 description 时要盯着这个数**。写长了不会报错，
只会让技能在 ZCode 里静默消失——最难查的一类故障。

---

## WorkBuddy

- 技能根 `~/.workbuddy/skills/`（本机存在，`[实测]`）
- MCP 配置 `~/.workbuddy/mcp.json`（`[据实现]`，来自客户端自身的配置约定）
- 上下文：`.workbuddy/memory/` 下的日志与长期记忆 + 工作区根 `AGENTS.md`

`doctor --client workbuddy` 会去读 `~/.workbuddy/mcp.json` 找 12306 注册。
**这台机器上该文件不存在**，所以报 `MISSING`——不是 bug，是真没配。

---

## 怎么自己验一个客户端

比翻文档可靠——**文档常落后于已装版本**。Electron 类客户端的技能加载逻辑
都在内核里，直接 grep 就能读到：

```bash
# 1. 找内核文件（Electron 应用一般是 app.asar 或 resources/<xxx>/<xxx>.cjs）
ls "<安装目录>/resources/"

# 2. 找技能目录常量
grep -o '"\.[a-z]*","skills"' <内核文件>

# 3. 找解析函数（常见名：resolveDefaultSkillRoots / loadSkills）
grep -o 'resolveDefaultSkillRoots.\{0,200\}' <内核文件>

# 4. 找上下文文件名
grep -o 'AGENTS\.md\|CLAUDE\.md\|GEMINI\.md' <内核文件>
```

**判定标准**：能直接读到目录常量与函数体 → 记 `[实测]`；
只能从项目代码反推 → 记 `[据实现]`。**别把后者写成前者。**

---

## 换客户端时的检查清单

1. 技能**装上了** ≠ 能**跑起来**——先确认该客户端放行 Python 执行
   （沙箱策略各客户端不同，这一条本项目尚未实测）
2. 跑 `doctor --client <名字>`，确认 MCP 与浏览器两项的状态
3. 检查 `description` 长度（见上表）
4. 多端共享真身的话，确认该客户端**跟随符号链接**
5. 跑一次完整链路：`render_html.py` → `consistency.py` → `ludbook_check.py`
