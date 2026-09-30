# 浏览器能力 · 能做什么、不能做什么

> C 组第 18 项要「四档视口无横向溢出」，第 20 项要「页面渲染截图亲验」。
> 本文档说清这两项在本机怎么落地，以及**两个会让你误报的工具陷阱**。

## 本机实测状态（2026-09-27）

| 能力 | 状态 |
|---|---|
| `agent-browser` | **未安装**（`command not found`），也没有 Playwright / Puppeteer 缓存 |
| Microsoft Edge | **可用** — `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`（Chromium 154） |
| Node / npx | v22.22.2 / 10.9.7 可用 |
| CDP 调试端口 | 可用（`--remote-debugging-port=9222`，实测能取到 `webSocketDebuggerUrl`） |

**结论**：不用装任何东西。Edge 是 Chromium 内核，自带无头模式。

---

## 工具：`tools/shoot.py`

```bash
# 溢出探针（四档视口）
python tools/shoot.py 路书_成都_渲染示例.html

# 额外截图
python tools/shoot.py 路书_成都_渲染示例.html --shot --outdir 截图/

# 自定义档位 / 结构化输出
python tools/shoot.py 路书_成都_渲染示例.html --widths 320,414,768
python tools/shoot.py 路书_成都_渲染示例.html --json
```

退出码：`0` 四档无溢出 ｜ `1` 有溢出 ｜ `2` 环境不可用（找不到浏览器）。

---

## ⚠️ 陷阱一：Edge 无头模式有 ~496px 的最小视口

`--window-size=320,900` **不会**给你 320px 的视口。实测（读 `innerWidth`）：

| 指定 | 实际 innerWidth |
|---|---|
| 320 | **496** ← 被抬到最小值 |
| 390 | **496** |
| 496 | 496 |
| 768 | **742** ← 另有 26px 窗口装饰开销 |
| 1280 | 1254 |

而**截图会被硬裁到指定宽度**。于是你会看到「320px 下标题、正文右侧全被切掉」——
**看起来就像页面横向溢出，其实是工具在裁图**。

> 差一点就这么报出去了。先读 `innerWidth`，别先下结论。

**绕法**：把路书塞进指定宽度的 `<iframe>`。media query 按 iframe 尺寸生效，
再用 `documentElement.scrollWidth > clientWidth` 精确判定。这正是 `shoot.py` 干的事。

**另一个必须带的参数**：iframe 里如果出现滚动条，会白占约 15px 宽度、
让本来贴合的内容「溢出」。真实手机用的是覆盖式滚动条，所以探针要加
`--hide-scrollbars`。**不带它，320px 一档会假报溢出。**

---

## ⚠️ 陷阱二：无头截图不滚动，入场动效不会触发

路书的进场动画是这么写的：

```css
html.js section .card, html.js section .day, ... { opacity: 0; ... }
```

```js
document.documentElement.classList.add('js');
```

有 JS → 加上 `js` 类 → 元素先隐藏，靠 `IntersectionObserver` 在进入视口时显示。
**而无头截图不滚动**，所以首屏之外的卡片全部停在 `opacity:0`——
截图里那一大片「淡得几乎看不见」的内容，**是这么来的，不是 bug**。

**验证办法**（比找浏览器 flag 可靠）：复制一份 HTML，把
`document.documentElement.classList.add('js')` 换成注释，再截图。
无 `js` 类 → 门控规则整条不生效 → 内容应当**全部清晰可见**。

**实测结果（2026-09-27，中山路书）**：无 JS 副本里天气卡、装备清单全部正常显示，
第 21 项（动效两态）**通过**。有 JS 时首屏渲染也正确。

---

## 能力边界

**能做**

- 静态页面的视口溢出检测、多档截图
- 渲染亲验（第 20 项）：确认页面真能渲染、版式没崩、字体图片都在
- JS 门控两态实测（第 21 项）：删掉 `js` 类就是「无 JS 态」

**不能做（本机现状）**

- **滚动到底再截图**：`--screenshot` 只拍视口。要看长页面得给超高的 `--window-size`，
  或用 CDP 的 `Page.captureScreenshot{captureBeyondViewport:true}`
- **精确 <496px 视口截图**：见陷阱一。溢出用探针测，截图别用它

**已补齐（2026-09-27）：CDP 驱动可用**——`tools/cdp_read.py`

原来这里写着「交互后截图需要 CDP，本机没装 WebSocket 客户端」。
实测发现**不需要装任何东西**：Node 22 有全局 `WebSocket`，
更干净的是**用 Python 标准库手写最小 WS 客户端**（RFC 6455，约 100 行），
于是 `tools/cdp_read.py` 零依赖跑了起来（Edge 154 / CDP Protocol 1.3）。

它能做的，是 `--dump-dom` **根本做不到**的那部分：

| 能力 | dump-dom | cdp_read.py |
|---|---|---|
| 首屏静态 DOM | ✅ | ✅ |
| 滚动触发懒加载（B站评论区） | ❌ 拿不到 | ✅ 实测拿到正文 |
| 穿透多层 Shadow DOM | ❌ | ✅ 实测穿透 45 层 |
| 判风控（按返回体特征） | 手工 | ✅ 内置 |

**踩到的坑（已固化进实现注释）**：拿 shadow root 的 `textContent` 当文本会**连 `<style>` 里的
CSS 一起取到**，开头是 `:host{...}`；第一版按这个前缀过滤，把含样式的那层**整块丢掉**——
而评论正文正躺在里面。正确做法是**按元素取叶子文本**，跳过 `STYLE/SCRIPT/LINK`。

> 只读边界照旧：`cdp_read.py` 只做「打开公开页 → 滚动 → 读渲染结果」。
> **不下载音视频、不导出 Cookie、不绕风控、不猜接口签名。**

---

## 与只读边界（铁律 8）的关系

浏览器自动化很容易越过那条线。**采集类动作必须停在只读一侧**：

- ✅ 可以：打开公开页面、读公开信息、截图自己产出的路书
- ❌ 不可以：绕过验证码 / 登录墙 / 风控；导出 Cookie / Token；
  代替用户登录；下单、占座、候补、改签、支付

遇到验证码或风控提示，**一次即停手换路径**（不是重试）。
查不到就按 `[D]` 标注并给用户人工确认方式——这是铁律 1 的常规出口，不是失败。
