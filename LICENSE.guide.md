# 许可指路牌（本仓库根目录）

根目录的 [`LICENSE`](LICENSE) 是**标准 MIT 文本**，覆盖本公开仓库的全部原创内容
（产品 `verified-travel-planner/`、示例 `产出示例/`、审计工具 `skill-doc-code-audit/`）。
本文件是那份许可的「使用说明书」——真正的许可义务细节在下面两处，**二次分发时必须随包保留**：

- `verified-travel-planner/LICENSE` —— 产品级 MIT，**内嵌两个上游项目的原始版权与许可声明**
- `verified-travel-planner/THIRD_PARTY_NOTICES.md` —— 上游与外部服务的完整声明、
  逐文件归属台账（17 个上游文件，机器核验）与使用边界

## 为什么有两层许可文件

本作品是两份 MIT 许可作品的**衍生作品**。MIT 的义务要求「保留原始版权声明与许可声明」，
所以产品目录里的 LICENSE 不能只写自己，必须原样内嵌上游声明；逐文件「哪些原样、
哪些改过、改了什么」登记在 THIRD_PARTY_NOTICES.md 的归属核对表里，并由
`tools/validate_skill.py` 机器校验（改了文件不更新台账，CI 直接红）。

根目录这层标准 MIT 的含义：**访客拿到本仓库任意内容，都可以按 MIT 自由使用**——
公开树里只有原创内容与 MIT 上游衍生内容，没有别的许可类型（第三方素材为零，
图片全部自绘；上游自带的铁路社区连接器从未搬入，见 THIRD_PARTY_NOTICES.md）。

## 仓库里有什么、没有什么

| 路径 | 性质 | 是否公开 |
|---|---|---|
| `verified-travel-planner/` | **产品**（skill 本体，含 LICENSE / THIRD_PARTY_NOTICES） | ✅ 公开 |
| `产出示例/` | **示例产出**：东莞（完整交付链路）+ 上海（路线/备选/顺道点）+ 成都（渲染基线桩件） | ✅ 公开 |
| `skill-doc-code-audit/` | 配套的文档↔代码一致性审计工具（另一个自建技能） | ✅ 公开 |
| `README.md`（对外）/ `WORKSPACE.md`（工作区）/ `AGENTS.md` / `.github/` | 说明与 CI | ✅ 公开 |
| `.workbuddy/`、`验收记录/` | 本地工作记录（**不在公开树内**，`.gitignore` 隔离） | 🔒 私有 |
| `~/.verified-travel-planner/` | 高德 key 与登录态 profile（**本来就不在仓库内**） | 🔒 私有 |

## 给二次分发者的三句话

1. 保留 `verified-travel-planner/` 内的两份合规文件，其余按根 MIT 随意用。
2. 改动上游衍生文件后，记得回来更新归属台账——CI 会替你盯。
3. 两个上游均为 MIT；商用前自查高德等外部平台的最新条款（见 THIRD_PARTY_NOTICES.md 的使用边界）。
