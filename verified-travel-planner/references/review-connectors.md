# 评论采集的接入规范（本地爬虫连接器 · AUTH 档）

> 本 skill 的**stdlib 内不内置任何平台爬虫**。这篇写的是「如果你想接」：
> 接什么、怎么接、红线在哪。对应 `social_source.py` 渠道矩阵里的
> `local_crawler`（AUTH 档）与 `crawler_saas`（OPTIONAL 档）。

---

## 一、为什么 stdlib 不内置爬虫

1. **风控是军备竞赛，不是可依赖的基建**：小红书/抖音 IP 风控（本机实测不可读）、
   大众点评字体反爬 + CSS 偏移 + mtgsig 签名、美团系 2025 年仍在对抗的签名参数。
   把这些包进 stdlib 等于承诺一个随时会断的能力——违背「能力先测后报」。
2. **合规姿态**：本工具全流程只读、不绕风控、一次即停；平台爬虫方案
   （如 MediaCrawler 的 CDP 真实浏览器路线）各有自己的法律与账号风险，
   应由使用者自行评估并自担——skill 只定义**接口**，不替人做决定。
3. **证据纪律**：爬来的评论在本 skill 里天花板是 `[C]`（信号），永不产出数字；
   为 `[C]` 信号引入高风险依赖，收益配不上代价。

## 二、连接器接口（本地爬虫要满足什么才算接入）

一个合规的评论连接器只需产出一个 JSON 文件（线索卡的 `notes[].reviews[]`
现成格式，`social_notes.py` 会照常校验）：

```json
{
  "title": "大众点评·XX 店评论区",
  "url": "https://www.dianping.com/shop/XXX",
  "connector_type": "community_mcp",
  "voice": "COMMENT",
  "checked_at": "2026-10-01",
  "place_evidence": [{"name": "XX 店"}],
  "claims": [],
  "reviews": [
    {"text": "评论原文（勿改写）", "stance": "POSITIVE",
     "source": "COMMENT", "account_hint": "昵称或留空",
     "date": "2026-09-30", "likes": 12}
  ]
}
```

校验方：`social_notes.py`（死线一视同仁：评论里的金额/时刻/车次照样被挡）、
`review_trust.py`（水军/推广信号整卡体检）、`echo_audit.py`（有 URL 的 claim
照常回声核查——**爬虫产出的 claim 不会因为是自己爬的就免检**）。

## 三、路线与风险对照（2026-10 调研快照）

| 路线 | 覆盖 | 关键风险 | 定位 |
|---|---|---|---|
| 本 skill 既有三通道（官方发布/搜索索引/用户投喂） | 官方口径 + 可索引内容 | 覆盖不了评论区深处 | **默认**，零额外风险 |
| CDP 真实浏览器（如 MediaCrawler 路线：扫码登录 + 登录态缓存 + 本机 Chrome 调试端口） | 小红书/抖音/B站/微博等七平台 | 账号风险、平台条款、仅供研究使用的合规声明 | AUTH 档，**用户自建自担** |
| OCR 破字体反爬（大众点评系） | 大众点评/美团 | 对抗最激烈（mtgsig 军备竞赛），法律灰度最高 | 不建议 |
| 商业 SaaS（Outscraper/RealDataAPI 等） | Google Maps/点评系 | 成本、数据条款、新鲜度 | OPTIONAL 档，海外 POI 可用 |

来源与实测依据：`references/social-sources.md`（21 渠道矩阵与本机实测）、
`references/echo-cross.md` 第四节（MediaCrawler / Fakespot / ReviewMeta /
大众点评治理规模的调研出处）。

## 四、红线（接了爬虫也不能碰的）

1. **死线不变**：评论/爬虫内容里的票价、营业时间、车次余票、距离车程，
   一律不得进路书——只能触发「去官方核实」的动作。
2. **不绕风控**：命中验证码/访问异常即停（`cdp_read.py` 的 RISK_MARKS 纪律）；
   不做代理池轮换硬闯。
3. **信号不结论**：`review_trust.py` 的输出是「排后面看」的依据，
   不删评论、不裁真假、不产「调整后评分」（ReviewMeta 式 adjusted rating
   与本项目「只出信号」哲学冲突，刻意不做）。
4. **留痕**：连接器产出的每条 review 都带 `checked_at` 与来源 URL，
   进不了证据链的东西宁可不要。
