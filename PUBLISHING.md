# 发布指南 · 把本仓库安全地发到 GitHub

这份文件写给「准备把本仓库公开」的人（未来的你）。它回答三件事：  
**哪些进公开仓、哪些不进、公开前还必须做什么。**

> ⚠️ **一句话警告**：`.gitignore` 只能阻止**将来**的提交，**管不了已经写进历史的东西**。  
> 本仓库的历史里曾经包含会话记忆与过程留痕，所以「加了 .gitignore」离「能公开」还差一步，  
> 见下面第三节。

---

## 一、公开范围

### ✅ 进公开仓

```
<仓库根>/
├── WORKSPACE.md                   ← 工作区说明（含公开范围表与待办）
├── README.md                      ← ★ 面向外部读者的项目说明（GitHub 首页，首屏带主图与 30 秒上手）
├── AGENTS.md                      ← AI 助手上下文入口
├── CONTRIBUTING.md                ← 贡献指南（分支/Fork + PR、闸门纪律、红线）
├── LICENSE                        ← 标准 MIT（GitHub 徽章识别）；指路牌在 LICENSE.guide.md
├── PUBLISHING.md                  ← 本文件
├── assets/                        ← README 主图（示例路书截图，2026-10-01 增）
├── docs/                          ← GitHub Pages 落地页（在线示例的入口，仅此一页）
├── .gitignore / .gitattributes    ← 忽略规则 / 禁换行转换（后者不能少，见下）
├── .github/workflows/gates.yml    ← CI：一键回归 + 凭据泄漏扫描
├── .github/workflows/pages.yml    ← Pages：部署在线示例（2026-10-01 增）
├── verified-travel-planner/       ← ★ 产品本体（可独立分发的 skill）
│   ├── SKILL.md / LICENSE / THIRD_PARTY_NOTICES.md
│   ├── engine/  tools/  references/  assets/  tests/
├── 产出示例/                       ← 示例产出（东莞完整链路 + 成都渲染基线）
└── skill-doc-code-audit/          ← 配套的文档↔代码一致性审计工具
```

### 🔒 只留本地（已被 `.gitignore` 排除，且已取消跟踪）

| 路径                                             | 里面是什么                      | 为什么不能公开                                   |
| ---------------------------------------------- | -------------------------- | ----------------------------------------- |
| `.workbuddy/`                                  | 会话记忆（开发全程日志 + 长期记忆）、应用发布登记 | 含本机绝对路径、本机用户名、内网地址、**一条已上线的路书链接**、行程当事人线索 |
| `验收记录/`                                        | 迁移 / 多端联接的**一次性脚本**、验收报告   | 脚本里硬编码了当时那台机器的路径；报告含本机路径                  |
| `.wbapp_*.genie`                               | 在线发布能力的标记文件                | 指向已发布的应用                                  |
| `产出示例/**/发布/`                                  | 静态发布副本                     | 发布产物是可再生产物，且含局域网二维码等                      |
| `~/.verified-travel-planner/`                  | 高德 key、登录态 profile         | **机器级凭据**（本来就不在仓库里）                       |
| `__pycache__/`、`*.bak-*`、`desource_report.txt` | 缓存与临时产物                    | 无价值、会噪声                                   |

> 关键点：**这些文件仍然在你本地磁盘上，一个都没删。** 取消跟踪（`git rm --cached`）  
> 只是让 git 不再管它们，文件照常使用——本项目的工作流（记忆自动加载、示例重渲染、  
> 一键回归）都不受影响。

### `.gitattributes` 不能删

仓库根的 `.gitattributes` 内容是 `* -text`（禁止 LF↔CRLF 转换）。  
删掉它在 Windows 上会让「渲染逐字节可复现」的基线永久失败——那是本项目最硬的一道回归。

---

## 二、忽略规则是什么

集中在 `.gitignore`，按五段组织、每段写了原因：**私密 / 凭据 / 发布产物 / 缓存 / 编辑器**。

自检（贴哪条路径就知道它会不会入库）：

```bash
git check-ignore -v <路径>     # 有输出 = 会被忽略（并告诉你是哪条规则）
```

挑入库的文件必须**同时**满足：不含本机路径、不含凭据、可由源码重新生成（或本身就是源）。

---

## 三、公开前还必须做的两件事

这两件事 `.gitignore` 都管不了，是本仓库真正的发布门槛。

### 3.1 清理历史里的私密副本

`.workbuddy/` 与 `验收记录/` 曾被提交过，历史提交里仍完整保留。  
**光取消跟踪不够——历史会跟着 `git push` 一起上去。**

有两条路，**两条都已在本机实验性克隆上实测**（不动真身）：

#### 路线 A：孤儿提交（历史清零，最稳）

```bash
# 在临时克隆里做，别在真身上试
git clone --no-hardlinks <本仓库> /tmp/pub && cd /tmp/pub
git checkout --orphan public        # 造一个没有父提交的新分支
git add -A                          # .gitignore 自动把私密项挡在外面
git commit -m "公开初始版本"
git branch -D main                  # ← 必须删掉旧分支
git push origin public:main         # ← 只推这一个 ref
```

- ✅ 实测结果：提交数 1、跟踪文件 85 个、历史中私密路径 **0** 次。
- ⚠️ **代价**：丢掉全部提交历史。
- ⚠️ **最容易踩的坑**：旧分支如果还在，`git log --all` 依然能看到全部私密内容。  
  **绝对不要 `git push --all`**——实测旧分支里私密路径出现 50 次，一推全泄。  
  要么先删旧分支，要么只推指定 ref（`git push origin public:main`）。

#### 路线 B：filter-repo 改写历史（保留历史，推荐）

需要 `git-filter-repo`（不是 git 自带的，本机未预装；已在本机隔离 venv 装过 2.47.0）：

```bash
pip install git-filter-repo -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

git clone --no-hardlinks <本仓库> /tmp/pub2 && cd /tmp/pub2
git filter-repo --force \
  --invert-paths --path .workbuddy --path 验收记录 \
  --email-callback 'return email.replace(b"<旧邮箱>", b"<新邮箱>")'
```

- ✅ 实测结果：历史中私密路径 **0** 次、旧邮箱出现 **0** 次、工作区完整、对象已重打包清理。
- ⚠️ **提交数会变**（实测 52 → 43）：只改动被删目录的提交会变成空提交，被自动剪掉。  
  这不是出错，是预期行为。
- ⚠️ **`origin` 会被摘掉**：filter-repo 默认删除远程，防止手滑推回原仓库。  
  push 之前记得 `git remote add origin <新仓库地址>`。
- ⚠️ 改写后所有提交哈希都变了，**不能**再和原仓库做普通 merge。

### 3.2 处理提交署名

每个提交都带作者署名（姓名 + 邮箱）。**这跟删不删文件无关，删文件也去不掉。**

- 路线 A（孤儿提交）里，新提交直接指定想要的署名即可：  
  `git -c user.name="<显示名>" -c user.email="<邮箱>" commit -m "公开初始版本"`  
  （或先在 GitHub 设置里开启邮箱隐私、用 `noreply` 地址）
- 路线 B 里用 `--email-callback` 一次改完历史所有署名（实测有效）。

**判断标准**：如果不想让这个邮箱和你的 GitHub 账号被关联起来，  
就要么改署名、要么用 GitHub 的 `noreply` 邮箱、要么保持仓库私有。

### 3.3 外部版 README（2026-09-30 已在真身完成）

```bash
git mv README.md WORKSPACE.md        # 工作区说明（含公开范围表，留着自己看）
git mv README.public.md README.md    # 外部读者版顶上去
```

**已完成**（2026-09-30，连同 `validate_skill.py` 的双文件扫描改造一次提交）：
GitHub 首页渲染的是根 `README.md`，现在访客第一眼读到的是对外说明；
工作区口径的表与待办都在 `WORKSPACE.md`。

> **2026-09-30 已解决（当时评估为「暂不做」，同日按正规解法落地）**：换名会牵动
> `validate_skill` 的自指校验——计数断言锚定在文件内容上，换名后计数行搬家，
> 实检数与文档声明失配。解法：`validate_skill.py` 把「README.md」一律展开为
> `README.md + WORKSPACE.md` 双文件扫描（`README_VARIANTS`），再把自述计数
> 按新实检数重排（50 → 55）。两棵树（本地/发布）文件相同 → 计数相同 → CI 绿。

### 3.4 顺带确认

- `.github/workflows/gates.yml` 里的 gitleaks 在**个人仓库**免费，组织仓库需自备  
  `GITLEAKS_LICENSE`。
- CI 跑的是 `python verified-travel-planner/tools/ship.py`（本机实跑过的那条命令）；  
  workflow 本身无法在本机执行，**首次 push 后看一眼它是否按预期触发**。
- `产出示例/东莞/` 是真实行程样本，含出发地、日期、人数与同行关系。  
  若要更中性，可把这几项替换成「某城市 / 某日期 / 若干人」。

---

## 四、发布前自检清单

发布（或每次大改后）按顺序跑：

```bash
# 1 一键回归：九道闸门 + 渲染基线，FAIL 退出码 2
python verified-travel-planner/tools/ship.py

# 2 文档引用可达性（公开仓里不应有断链）
python skill-doc-code-audit/audit.py verified-travel-planner

# 3 私密项隔离自检：每条都应打印出「命中了哪条规则」（= 会被忽略）
git check-ignore -v .workbuddy/memory/MEMORY.md 验收记录/README.md .wbapp_*.genie

# 4 公开树里不该出现真实用户名 / 邮箱 / 内网地址 / 上线链接（**应无输出**）
git grep -nE 'C:\\Users\\[A-Za-z]|[0-9]+@[A-Za-z]+\.|192\.168\.|workbuddy\.host' \
  -- README.md AGENTS.md LICENSE PUBLISHING.md
```

> **判据的边界（已实测确认）**：上面前 3 条目前都是空/无输出，第 4 条命中 0 个文件。
>
> 而 `D:\旅游\`（工作区路径）**是刻意保留的**：README / AGENTS 描述的本来就是  
> 「这个工作区长什么样」，它不含个人信息，只是对本机以外的人没有意义。  
> 如果你希望公开版读起来更通用，可以再写一份面向外部读者的 README，  
> 把工作区路径换成 `<工作区>`——这属于可选的润色，不是发布门槛。

- [ ] `ship.py` 全绿
- [x] 文档审计 EXIT 0
- [x] `git status --untracked-files=all` 里没有会误入库的私密文件
- [ ] 公开树里没有本机绝对路径、内网地址、上线链接、邮箱
- [ ] 历史已清理（路线 A 或 B 之一），且**只推指定分支**
- [ ] 署名已按需处理
- [ ] CI 首次触发正常
