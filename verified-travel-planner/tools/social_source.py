#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
social_source.py — 社媒采集渠道的能力矩阵与采集计划（阶段 3.5 的「工具箱」）

回答两个问题：
  1. 这台机器上现在到底能从哪些渠道拿到社媒内容？            → `--doctor`
  2. 给定一个目的地，这一趟采集该怎么排？                    → `--plan 目的地`

设计原则（与项目铁律一致）
  ① **报「没有」比报假的「有」值钱**——探测不到就如实标未配置，不猜、不假装。
  ② **证据等级天花板写在渠道上**，不是写在内容上。渠道决定这根线，内容再好也越不过去。
  ③ **风控一次即停手**（铁律 8）——`BLOCKED` 渠道永远不会被 `--plan` 选中，
     也不接受「换个 UA 再试试」这种绕法。
  ④ **只读边界**——本脚本只做渠道登记与计划生成，不执行任何采集、不写任何 Cookie。

五档渠道状态
  OPEN      零配置、本机实测可用
  OPTIONAL  需一次人工配置（装连接器 / 填 key / 用户投喂）
  AUTH      需**用户授权登录**——技术可行，但风险落在用户自己的账号上
  GATED     需企业资质或付费订阅，当前未配置——**不是不可用，是没开**
  BLOCKED   实测风控不可达，明确放弃（写在这里是为了防止反复重试）

AUTH 档的一条总纲（**别读漏**）
  登录只提升**覆盖度**，不提升**证据等级**。小红书笔记无论怎么读，都是 UGC，
  天花板照旧 `[C·单源]`——因为 `[A]` 的定义是「工具实证」，`[B]` 是「两个独立来源互证」，
  两者都不是「读得更多」能换来的。所以这一档的收益是「多几条线索」，
  不是「线索更可信」；而代价是账号风险，且**风险不在 AI 身上，在用户身上**。

用法
----
    python social_source.py --doctor                # 能力矩阵（本机实测状态）
    python social_source.py --doctor --live         # 附带网络连通性探测
    python social_source.py --plan 中山 --days 3    # 生成采集计划
    python social_source.py --json                  # 结构化输出

退出码：`0` 正常 ｜ `1` 执行出错 ｜ `2` 无任何可用渠道（阶段 3.5 必须写跳过理由）
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
from pathlib import Path

# ============================ 渠道注册表 ============================
# ceiling：该渠道产出内容的**证据等级上限**（见 references/evidence-rules.md）
#   B = 官方发布 + 媒体独立转载，或两个独立来源互证
#   C = 单源线索（UGC、攻略站、索引转载）
# 死线：任何渠道都不许把「票价 / 营业时间 / 车次余票 / 距离车程」送进路书。

PROVIDERS = [
    # ---------------- OPEN：零配置可用 ----------------
    {
        'id': 'gov_official',
        'name': '官方文旅门户 + 权威媒体转载',
        'tier': 'OPEN',
        'ceiling': 'B',
        'why': '官方发布配合媒体独立转载，构成两个独立来源',
        'method': '搜索引擎定位 → 直接读门户正文',
        'yield': '每目的地 2–6 条有效线索',
        'limit': '只给「线路组合 + 玩法定性」，不给体验细节与人流实况',
        'compliance': '公开政务信息；署名 + 访问日期即可',
        'parse': '活动档期必须落成「起止日期 + 主会场」两个字段，缺一不可',
    },
    {
        'id': 'ota_guide',
        'name': 'OTA 攻略站（携程 / 同程）',
        'tier': 'OPEN',
        'ceiling': 'C',
        'why': '平台编辑与用户共建，单一平台内不可自证',
        'method': '**URL 必须来自搜索索引，不可自己拼**——拼错的 URL 会静默落到别的目的地',
        'yield': '每目的地 3–8 条动线级线索',
        'limit': '动线常为「一天打包」，与本项目按车程反推的排法需要重新校验',
        'compliance': '摘要引用 + 出处链接；不得整篇转载',
        'parse': '重点是**动线顺序与耗时区间**，可用来交叉验证自排行程',
        'proven': '2026-09-27 实测：手拼的 `you.ctrip.com/place/zhongshan1215.html` '
                  '实际返回**「沃斯」攻略页**——正是 `geomatch` 警告的那类地理错配，'
                  '所以这条渠道的入口一律用搜索结果给的 url',
    },
    {
        'id': 'search_index',
        'name': '搜索引擎索引',
        'tier': 'OPEN',
        'ceiling': 'C',
        'why': '索引摘要与原文同源，**不构成第二来源**',
        'method': '关键词检索 → 命中页直接读（不是读摘要）',
        'yield': '每轮 5–10 条命中，可筛出 2–4 条有效',
        'limit': '摘要不可当独立来源；同源转载需要识别',
        'compliance': '只读公开页；遵守 robots 与站点条款',
        'parse': '必须落到原页 url，摘要一律不写进线索卡',
    },
    {
        'id': 'wechat_mp',
        'name': '微信公众号图文（文旅局 / 地方媒体号）',
        'tier': 'OPEN',
        'ceiling': 'B',
        'why': '政府或媒体主体运营的公众号，配合门户发布可互证',
        'method': '搜索引擎索引到 mp.weixin.qq.com 正文 → 无头浏览器取 DOM',
        'yield': '每目的地 1–4 条，档期类信息命中率高',
        'limit': '正文有反盗链与外链限制，只能读文字',
        'compliance': '公众号图文著作权归作者；仅摘录事实性信息并署名',
        'parse': '图文混排多，抽取正文后要先剥「点击蓝字关注」类模板段',
        'proven': '2026-09-27 实测：`mp.weixin.qq.com/s/…` 取 DOM 得 4.7 MB、'
                  '正文块 22 个，命中官方发布原文（《十大热门景区、八条精品旅游线路公布》）',
    },
    {
        'id': 'weibo_search',
        'name': '微博移动版搜索',
        'tier': 'OPEN',
        'ceiling': 'C',
        'why': '个人与机构混发，单条不可自证',
        'method': 'm.weibo.cn 搜索页 → 无头浏览器取 DOM（移动版比 PC 版松）',
        'yield': '一页 10–20 条，热搜与实时性最好',
        'limit': '删帖快、时效短；营销号占比高',
        'compliance': '只读公开微博；不互动、不采个人信息',
        'parse': '短文本为主，含话题标签与转述，**必须回原发账号**再定级',
        'proven': '2026-09-27 实测：搜索页取 DOM 得 93 KB、内容块 147 个，**无风控标记**',
    },
    {
        'id': 'bili_search',
        'name': 'B站搜索页（标题 / UP主 / 播放量）',
        'tier': 'OPEN',
        'ceiling': 'C',
        'why': 'UP 主 UGC，单源',
        'method': 'search.bilibili.com → 无头浏览器取 DOM',
        'yield': '一页 30–45 条卡片，可出 5 个字段的结构化条目',
        'limit': '**拿不到视频里的口播内容**——除非 UP 主传了字幕，否则只能靠用户投喂'
                 '（见 `references/transcript-and-comments.md`）；搜索页拿不到简介；点赞数取不到',
        'compliance': '只读公开页；不下载视频、不绕过登录',
        'parse': '搜索页可直接出条目：标题 / UP主 / 时长 / 播放量 / 链接（2026-09-27 实测 42/42 完整）；'
                 '播放量要跳过前置的图标 SVG 才取得到数字；'
                 '**关键词会混入无关内容**（实测搜「中山饮食」混进房价与增肥 vlog），须按关键词二次过滤；'
                 '**评论区要用 `tools/cdp_read.py` 滚动触发**——dump-dom 拿不到，'
                 '且评论在嵌套 45 层的 Shadow DOM 里（不滚动≠风控）；'
                 '页面上的「登录」按钮文案会造成假阳性，判风控要看返回体不看关键词',
        'proven': '2026-09-27 实测：搜索页 DOM 1.1 MB / 42 张卡片，无风控，字段完整率 42/42；'
                  '详情页可读标题、UP主与**完整简介**；'
                  '**未登录下滚动即读到评论正文**（3/6 条，正反评价都在）。'
                  '⚠️ 详情页出现的 `-352` 是 CSS 里 `background-position:-3520px` 造成的**假阳性，不是风控码**',
    },
    {
        'id': 'browser_probe',
        'name': '无头浏览器探针（公开页 DOM）',
        'tier': 'OPEN',
        'ceiling': 'C',
        'why': '拿到的仍是公开页内容，单源照旧',
        'method': 'Edge --headless=new --dump-dom（见 references/browser-use.md）',
        'yield': '按页计，一页一条',
        'limit': 'JS 渲染页需 virtual-time-budget；最小视口 ~496px',
        'compliance': '只读公开页，不模拟登录、不绕验证码',
        'parse': 'dump 出的 DOM 需去导航/页脚模板，否则噪声淹没正文',
    },

    # ---------------- OPTIONAL：需一次配置 ----------------
    {
        'id': 'connector_scrape',
        'name': '抓取类连接器（Firecrawl / Tavily / Exa 等聚合）',
        'tier': 'OPTIONAL',
        'ceiling': 'C',
        'why': '本质是托管代理抓公开页，换了个出口 IP，证据等级不升',
        'method': '装连接器 → 调用其 search / scrape 工具',
        'yield': '代理通常按次计费，单次采集预算 10–30 次调用',
        'limit': '出口 IP 池会轮换，风控站点仍可能拦；按调用计费',
        'compliance': '多数代理服务条款禁止用于抓取需登录/付费墙内容',
        'parse': '返回多为 markdown，模板段仍需清洗',
    },
    {
        'id': 'connector_social',
        'name': '社媒数据连接器（社媒内容 / 创作者 / 趋势数据）',
        'tier': 'OPTIONAL',
        'ceiling': 'C',
        'why': '第三方聚合数据，不是平台一手的工具实证，不能算 [A]',
        'method': '装连接器 → 按关键词/话题取内容与互动数据',
        'yield': '按套餐配额，可能一次拿到数十条',
        'limit': '**国内平台覆盖普遍弱于海外**（TikTok / YouTube / IG 强，小红书 / 抖音弱）',
        'compliance': '只取公开内容与聚合指标；不落库个人信息（账号昵称可，联系方式不可）',
        'parse': '互动数据（赞藏评）只能当**热度信号**，不能当质量证明',
    },
    {
        'id': 'crawler_saas',
        'name': '云采集 SaaS（可视化采集器）',
        'tier': 'OPTIONAL',
        'ceiling': 'C',
        'why': '采集结果仍是公开页内容，单源照旧',
        'method': '在平台上建模板（列表页 → 详情页字段）→ 触发任务 → 导出结构化数据',
        'yield': '一次任务可拿数百条，是「消息数量」提升最直接的渠道',
        'limit': '需自建并维护模板；平台风控升级会让模板失效',
        'compliance': '**采集者自担合规责任**；不得采集个人信息、不得绕过登录与验证码',
        'parse': '导出即结构化，重点是去重（同篇多账号转载）与时效过滤',
    },
    {
        'id': 'user_provided',
        'name': '用户投喂（分享链接 / 截图 / 导出文件）',
        'tier': 'OPTIONAL',
        'ceiling': 'C',
        'why': '真实用户能打开采集端打不开的页面，但仍是单源',
        'method': '用户提供 url 或截图 → 走只读读取',
        'yield': '按投喂量',
        'limit': '依赖用户主动；截图需人工转录，转录即可能失真',
        'compliance': '仅用于本人行程；不影响他人账号，不二次分发',
        'parse': '线索卡必须记平台与账号 ID；截图来源须注明并标记为人工转录',
    },
    # ---------------- AUTH：需用户授权登录（风险落在用户账号上）----------------
    # 这一档的共性：技术上必须「用你的登录态」才读得到内容。
    # **关键判断：登录只提升覆盖度，不提升证据等级**——内容仍是 UGC，天花板照旧 [C·单源]。
    # 三者强度递增、风险也递增：人工辅助 < 半自动读取 < 批量采集。
    {
        'id': 'logged_in_cdp',
        'name': 'CDP 接管已登录浏览器（人工导航 + 自动读取）',
        'tier': 'AUTH',
        'ceiling': 'C',
        'why': '登录越过的只是账号墙；笔记正文仍是单源 UGC',
        'method': '独立 profile 扫码登录一次 → 之后 headless 复用该 profile 读页面 DOM'
                  '（tools/social_login.py --launch / --grab）',
        'yield': '按人工导航页数计，一趟 5–20 页',
        'limit': '**复用登录态必须独占 profile**——主浏览器在运行时就拿不到它的登录态'
                 '（2026-09-27 实测：Edge 对 Cookies 持独占锁，共享读报 err=32；'
                 '同 user-data-dir 起第二个实例直接失败）',
        'compliance': '只读本人可见的公开笔记；**不做批量翻页、不做并发**——'
                      '一旦自动化，风险从「违反平台条款」升级到「你的账号被限」',
        'parse': 'dump 出的 DOM 去模板段后取正文；仍需逐条记 url + 查看时间',
        'proven': '2026-09-27 实测：本机 Edge 154.0.4258.37 + CDP Protocol 1.3 通路可用'
                  '（独立 profile 起 headless，/json/version 与 /json/list 均通，7 个可操作标签页）',
    },
    {
        'id': 'local_crawler',
        'name': '本地采集工具（MediaCrawler 类，扫码登录 + 批量翻页）',
        'tier': 'AUTH',
        'ceiling': 'C',
        'why': '批量拿到的仍是平台 UGC，单条不可自证',
        'method': '下载开源项目 → Playwright/CDP 扫码登录 → 按关键词批量采（含评论）',
        'yield': '一次任务可拿数百条，**是「消息数量」提升最猛的一条**',
        'limit': '**许可证多为非商业**（MediaCrawler 用 NON-COMMERCIAL LEARNING LICENSE，'
                 '不是 MIT）；**走「签名对抗」路线**——平台一升级算法即失效，要持续跟修；'
                 '**账号风险真实存在**：项目文档自己写明「会触发风控，遇验证须停手人工处理」',
        'compliance': '**平台 ToS 普遍禁止自动化采集**；本项目只读边界下不建议默认启用，'
                      '要用须用户显式拍板并自担账号后果',
        'parse': '导出即结构化；重点是去重（同篇多账号转载）与时效过滤',
        'proven': '2026-09-27 核实：仓库 65,797 stars、许可 NOASSERTION/Other（非 MIT）、'
                  '2026-09-19 仍在更新；本机 github.com 网页超时但 codeload/raw/api 三域可达'
                  '——**能装，但要走 tarball 而不是 git clone**',
    },
    {
        'id': 'third_party_api',
        'name': '第三方社媒数据 API（TikHub 类，按次付费）',
        'tier': 'AUTH',
        'ceiling': 'C',
        'why': '第三方聚合的是平台数据，不是平台一手的工具实证，**上不到 [A]**',
        'method': '注册取 token → 调 REST 接口（有中国加速域）→ 结构化 JSON',
        'yield': '按调用量；免费额度约 50 次，之后 $0.001–0.01 / 次',
        'limit': '**付费且按次计费**——调试时同参数重拉容易烧额度；'
                 '数据口径与平台原生不一致，跨平台不可直接比大小',
        'compliance': '服务商主张「仅采集公开数据」；合规责任仍在调用方，'
                      '不得转售、不得公开原始数据集',
        'parse': '响应结构化、字段比自采稳定；互动指标只能作**热度信号**',
        'proven': '2026-09-27 实测：本机 `api.tikhub.io` 可达（HTTP 200）',
    },

    # ---------------- GATED：需资质 / 付费 ----------------
    {
        'id': 'platform_openapi',
        'name': '平台开放平台（内容/趋势类官方接口）',
        'tier': 'GATED',
        'ceiling': 'A',
        'why': '官方接口返回属工具实证，可到 [A]——**这是唯一能到 A 的社媒渠道**',
        'method': '注册开发者 → 建应用 → 审核通过 → OAuth 取数',
        'yield': '按接口配额，通常为最稳定的量',
        'limit': '**国内内容平台开放平台多面向企业主体或广告/电商场景**，'
                 '纯旅行攻略类的读接口通常不开；审核周期与资质门槛是主要卡点',
        'compliance': '严格按平台授权范围与接口条款；越权取数会被封应用',
        'parse': '字段规范稳定，是最省解析成本的一档',
    },
    {
        'id': 'third_party_panel',
        'name': '第三方数据面板（内容/达人/趋势订阅）',
        'tier': 'GATED',
        'ceiling': 'C',
        'why': '第三方聚合，非平台一手实证，天花板上不到 [A]',
        'method': '购买订阅 → 平台内查询 / 导出',
        'yield': '量大，且带互动指标，适合做「热度排序」',
        'limit': '**付费**；数据口径各家不同，跨平台不可直接比大小',
        'compliance': '遵守订阅协议，不转售、不公开原始数据集',
        'parse': '互动指标只能作热度信号；**必须与内容文本一起看**，避免「高赞低质」',
    },

    # ---------------- BLOCKED：实测不可达 ----------------
    {
        'id': 'direct_xhs',
        'name': '小红书匿名直连（首页 / 搜索 / 笔记详情）',
        'tier': 'BLOCKED',
        'ceiling': '—',
        'why': '2026-09-27 本机无头 Edge 匿名实测：三类页面**全部**返「安全限制 · IP存在风险 · 300012」',
        'method': '已停用',
        'yield': '0',
        'limit': '**是风控拦截，不是登录墙**——「登录一下就能读」是错判',
        'compliance': '不绕过（换 UA / 换 IP / 打验证码接口均属越线）',
        'parse': '—',
    },
    {
        'id': 'direct_douyin',
        'name': '抖音匿名直连（搜索页 / 发现页）',
        'tier': 'BLOCKED',
        'ceiling': '—',
        'why': '2026-09-27 本机无头 Edge 匿名实测：搜索页返「验证码中间页」',
        'method': '已停用',
        'yield': '0',
        'limit': '同上，风控而非登录墙',
        'compliance': '不绕过',
        'parse': '—',
    },
    {
        'id': 'zhihu_search',
        'name': '知乎搜索页',
        'tier': 'BLOCKED',
        'ceiling': '—',
        'why': '2026-09-27 实测直接返回 JSON 风控体：「您当前请求存在异常，暂时限制本次访问」',
        'method': '已停用',
        'yield': '0',
        'limit': '接口级风控，比页面级更硬',
        'compliance': '不绕过',
        'parse': '—',
        'proven': '返回体仅 284 字节，是全套实测里最短的一个——**空响应本身就是证据**',
    },
    {
        'id': 'mafengwo',
        'name': '马蜂窝攻略页',
        'tier': 'BLOCKED',
        'ceiling': '—',
        'why': '2026-09-27 实测返回「WAF 拦截页面」（含 `submitWafFeedback` 脚本）',
        'method': '已停用',
        'yield': '0',
        'limit': '站点级 WAF，非账号问题',
        'compliance': '不绕过',
        'parse': '—',
        'proven': '响应仅 2.6 KB，页面标题即「WAF拦截页面」',
    },
    {
        'id': 'qyer',
        'name': '穷游攻略页',
        'tier': 'BLOCKED',
        'ceiling': '—',
        'why': '2026-09-27 实测返回 `503 Service Temporarily Unavailable`',
        'method': '暂不可用（**是上游服务不可用，不是风控**——过段时间可以重试一次）',
        'yield': '0',
        'limit': '与风控不同：属于可用性波动，值得择日复测',
        'compliance': '—',
        'parse': '—',
    },
]

TIER_ORDER = ['OPEN', 'OPTIONAL', 'AUTH', 'GATED', 'BLOCKED']
TIER_DESC = {
    'OPEN': '零配置 · 本机实测可用',
    'OPTIONAL': '需一次人工配置',
    'AUTH': '需用户授权登录 · **风险落在你的账号上**（登录不升证据等级）',
    'GATED': '需资质或付费 · 当前未配置',
    'BLOCKED': '实测不可达 · 已放弃（风控 / WAF / 服务不可用）',
}

# ============================ 本机探测 ============================

_MCP_HINTS = ('firecrawl', 'tavily', 'exa', 'scrap', 'crawl', 'social',
              'realtrace', 'bazhuayu', 'agent-earth', 'earth')
_KEY_HINTS = ('FIRECRAWL_API_KEY', 'TAVILY_API_KEY', 'EXA_API_KEY',
              'JINA_API_KEY', 'SERPAPI_API_KEY', 'SCRAPINGBEE_API_KEY')
# 连接器 key 命中后，哪个渠道算配好了
_TIER_OVERRIDE = {
    'connector_scrape': ('FIRECRAWL_API_KEY', 'TAVILY_API_KEY', 'EXA_API_KEY',
                         'JINA_API_KEY', 'SERPAPI_API_KEY', 'SCRAPINGBEE_API_KEY'),
    'connector_social': ('REALTRACE_API_KEY', 'AGENT_EARTH_API_KEY'),
    'crawler_saas': ('BAZHUAYU_API_KEY',),
    'platform_openapi': ('XHS_APP_ID', 'DOUYIN_CLIENT_KEY', 'BILI_APP_KEY'),
    'third_party_panel': ('XINBANG_TOKEN', 'CHANMAMA_TOKEN', 'QIANGUA_TOKEN'),
    'third_party_api': ('TIKHUB_API_KEY', 'TIKHUB_API_TOKEN'),
}

# AUTH 档的独立 profile 目录（与 credentials.json 同根，便于统一清理）
SOCIAL_PROFILE_DIR = Path.home() / '.verified-travel-planner' / 'social-profile'


def _probe_browser() -> dict:
    """探测本机浏览器与「登录态 profile」状态。

    只检查**可执行文件是否存在**与**profile 目录是否有 Cookies 文件**，
    不读取、不解密、不导出任何 Cookie 内容。
    """
    env = os.environ
    cands = [
        Path(env.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)'))
        / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe',
        Path(env.get('PROGRAMFILES', r'C:\Program Files'))
        / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe',
        Path(env.get('PROGRAMFILES', r'C:\Program Files'))
        / 'Google' / 'Chrome' / 'Application' / 'chrome.exe',
        Path(env.get('LOCALAPPDATA', '.'))
        / 'Google' / 'Chrome' / 'Application' / 'chrome.exe',
    ]
    exe = next((p for p in cands if p.is_file()), None)
    ck = SOCIAL_PROFILE_DIR / 'Default' / 'Network' / 'Cookies'
    ck_bytes = ck.stat().st_size if ck.is_file() else 0
    return {'exe': str(exe) if exe else None,
            'profile': str(SOCIAL_PROFILE_DIR) if SOCIAL_PROFILE_DIR.is_dir() else None,
            'profile_cookies_bytes': ck_bytes}


def _probe_config() -> dict:
    """探测本机配置：MCP 服务器、相关环境变量、浏览器自动化插件。"""
    home = Path.home()
    mcps = []
    for cand in (home / '.workbuddy' / 'mcp.json',
                 home / '.workbuddy' / '.mcp.json',
                 Path.cwd() / '.mcp.json'):
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding='utf-8'))
            except Exception:
                continue
            mcps += list((data.get('mcpServers') or {}).keys())
    mcp = sorted({m for m in mcps
                  if any(h in m.lower() for h in _MCP_HINTS)})

    keys = [k for k in _KEY_HINTS if os.environ.get(k)]
    extra_keys = [v for vs in _TIER_OVERRIDE.values() for v in vs]
    keys += [k for k in extra_keys if os.environ.get(k) and k not in keys]

    browser_plugin = None
    cache = home / '.workbuddy' / 'plugins' / 'cache'
    if cache.is_dir():
        for p in cache.rglob('agent-browser'):
            if p.is_dir():
                browser_plugin = str(p)
                break
    return {'mcp_servers': mcp, 'api_keys': sorted(set(keys)),
            'browser_plugin': browser_plugin, 'browser': _probe_browser()}


def _probe_live(timeout: float = 4.0) -> dict:
    """网络连通性：只探中立公开站点，不探风控站点（探了也只是再挨一次拦）。"""
    targets = [('公共 DNS 解析', 'www.gov.cn', 443),
               ('HTTPS 出口', 'mp.weixin.qq.com', 443),
               ('搜索引擎出口', 'www.bing.com', 443)]
    out = {}
    for label, host, port in targets:
        try:
            socket.setdefaulttimeout(timeout)
            with socket.create_connection((host, port), timeout=timeout):
                out[label] = 'REACHABLE'
        except Exception as exc:                      # noqa: BLE001
            out[label] = 'UNREACHABLE (%s)' % type(exc).__name__
    return out


def resolve(probe: dict) -> list:
    """把注册表 + 本机探测结果合并成运行时的渠道清单。"""
    rows = []
    for p in PROVIDERS:
        row = dict(p)
        st = 'READY' if p['tier'] == 'OPEN' else 'UNCONFIGURED'
        note = ''
        if p['tier'] == 'BLOCKED':
            st = 'BLOCKED'
            if p['id'] == 'qyer':
                note = '上游 503，属可用性波动——可择日重试一次'
            elif p['id'] == 'mafengwo':
                note = '站点级 WAF，非账号问题，不重试'
            elif p['id'] == 'zhihu_search':
                note = '接口级风控，比页面级更硬，不重试'
            else:
                note = '风控实测拦截，不重试'
        elif p['id'] == 'user_provided':
            st, note = 'READY', '等待用户投喂（随时可用，不依赖配置）'
        elif p['id'] == 'connector_scrape':
            if probe['mcp_servers'] or any(k in probe['api_keys'] for k in _TIER_OVERRIDE[p['id']]):
                st, note = 'READY', '探测到 MCP 服务器或抓取类 key'
        elif p['id'] == 'connector_social':
            hit = [m for m in probe['mcp_servers']
                   if any(h in m.lower() for h in ('social', 'realtrace', 'earth'))]
            if hit or any(k in probe['api_keys'] for k in _TIER_OVERRIDE[p['id']]):
                st, note = 'READY', '探测到社媒类连接器'
        elif p['id'] == 'crawler_saas':
            if any(k in probe['api_keys'] for k in _TIER_OVERRIDE[p['id']]):
                st, note = 'READY', '探测到采集 SaaS token'
        elif p['id'] == 'logged_in_cdp':
            br = probe.get('browser') or {}
            if br.get('exe'):
                st = 'READY'
                if br.get('profile_cookies_bytes'):
                    note = ('CDP 通路可用 + 登录态 profile 已建（Cookies %d 字节）'
                            '——可直接 --grab；**登录态只提升覆盖度，不升证据等级**'
                            % br['profile_cookies_bytes'])
                else:
                    note = ('CDP 通路可用；登录态 profile 未建——'
                            '先跑 social_login.py --launch 扫码一次')
            else:
                note = '未探测到 Edge / Chrome 可执行文件'
        elif p['id'] == 'local_crawler':
            st, note = 'UNCONFIGURED', ('需自行下载安装；**非商业许可 + 账号风险**，'
                                        '默认不启用，要用须显式拍板')
        elif p['id'] == 'third_party_api':
            if any(k in probe['api_keys'] for k in _TIER_OVERRIDE[p['id']]):
                st, note = 'READY', '探测到第三方 API token（按次计费，留意额度）'
            else:
                note = '未配置 token（免费额度约 50 次，之后按次付费）'
        elif p['id'] == 'platform_openapi':
            if any(k in probe['api_keys'] for k in _TIER_OVERRIDE[p['id']]):
                st, note = 'READY', '探测到平台开放平台凭据'
        elif p['id'] == 'third_party_panel':
            if any(k in probe['api_keys'] for k in _TIER_OVERRIDE[p['id']]):
                st, note = 'READY', '探测到数据面板 token'
        row['status'] = st
        row['status_note'] = note
        rows.append(row)
    return rows


# ============================ 采集计划 ============================

# 关键词组：每类都是「社媒能答、地图答不了」的问法
QUERY_TEMPLATES = [
    ('档期', '{city} {month} 文旅活动 精品线路'),
    ('官方', '{city} 文旅局 发布 精品线路 十大景区'),
    ('动线', '{city} 两天 攻略 路线 顺序'),
    ('小众', '{city} 小众 冷门 值得去'),
    ('机位', '{city} 拍照 机位 出片 日落'),
    ('吃', '{city} 本地人 吃什么 老店'),
    ('避雷', '{city} 避雷 踩坑 不值得'),
    ('时节', '{city} {month} 天气 穿搭 时令'),
]


def build_plan(city: str, days: int, month: str, rows: list) -> dict:
    usable = [r for r in rows if r['status'] in ('READY',) and r['ceiling'] != '—']
    usable.sort(key=lambda r: (TIER_ORDER.index(r['tier']), r['ceiling']))
    queries = [{'kind': k, 'query': t.format(city=city, month=month)}
               for k, t in QUERY_TEMPLATES]
    return {
        'destination': city,
        'days': days,
        'month': month,
        'channels': [{'id': r['id'], 'name': r['name'], 'tier': r['tier'],
                      'ceiling': r['ceiling']} for r in usable],
        'queries': queries,
        'budget': {
            'search_calls': max(6, days * 2),
            'fetch_pages_per_query': 2,
            'note': '代理/连接器按次计费时，先跑「官方」与「档期」两类，命中率最高',
        },
        'deadlines': ['票价', '营业时间', '车次余票', '距离车程'],
    }


# ============================ 输出 ============================

def print_doctor(rows: list, probe: dict, live: dict | None) -> int:
    W = 78
    print('=' * W)
    print('社媒采集能力矩阵　本机 %s' % platform.node())
    print('=' * W)
    for tier in TIER_ORDER:
        group = [r for r in rows if r['tier'] == tier]
        if not group:
            continue
        print('\n【%s】%s' % (tier, TIER_DESC[tier]))
        if tier == 'AUTH':
            print('  ⚠️ 总纲：登录只提升**覆盖度**，不提升**证据等级**——'
                  'UGC 天花板照旧 [C·单源]；')
            print('     而账号风险由**你**承担，不由工具承担。强度递增：'
                  '人工辅助 < 半自动读取 < 批量采集。')
        for r in group:
            mark = {'READY': '✅', 'UNCONFIGURED': '⚪', 'BLOCKED': '⛔'}[r['status']]
            print('  %s %-14s %s' % (mark, r['id'], r['name']))
            print('      证据上限 %s ｜ %s' % (r['ceiling'], r['why']))
            print('      产出 %s' % r['yield'])
            if r.get('proven'):
                print('      实证 %s' % r['proven'])
            if r['status_note']:
                print('      状态 %s' % r['status_note'])

    ready = [r for r in rows if r['status'] == 'READY']
    # 按 A>B>C>D 排序取最高（**不能直接 max 字符串**——ASCII 里 'C' > 'B'）
    rank = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
    cands = [r['ceiling'] for r in ready if r['ceiling'] in rank]
    top = min(cands, key=lambda c: rank[c]) if cands else '—'
    print('\n' + '-' * W)
    print('可用渠道 %d / %d ｜ 本机可达的最高证据等级 %s' % (len(ready), len(rows), top))
    if top != 'A':
        print('  ⚠️ 无 [A] 级社媒渠道——社媒情报**天花板为 [B]**（官方发布互证）。')
        print('     想上 [A] 只一条路：平台官方开放平台接口（见 GATED 档，需资质）。')
        print('     **AUTH 档不改变这个天花板**——它解决「读得到」，不解决「更可信」。')
    print('  死线不变：票价 / 营业时间 / 车次余票 / 距离车程 —— 任何渠道都不得送进路书。')

    print('\n本机探测')
    print('  MCP 服务器（社媒/抓取相关）：%s' % (probe['mcp_servers'] or '无'))
    print('  相关 API key：%s' % (probe['api_keys'] or '无'))
    print('  浏览器自动化插件：%s' % (probe['browser_plugin'] or '无'))
    br = probe.get('browser') or {}
    print('  本机浏览器：%s' % (br.get('exe') or '未探测到 Edge / Chrome'))
    if br.get('profile_cookies_bytes'):
        print('  登录态 profile：已建（Cookies %d 字节）｜ %s'
              % (br['profile_cookies_bytes'], br.get('profile')))
    else:
        print('  登录态 profile：未建（AUTH 档需先跑 social_login.py --launch）')
    if live:
        for k, v in live.items():
            print('  %s：%s' % (k, v))

    if not ready:
        print('\n❌ 无任何可用渠道——阶段 3.5 必须写跳过理由（第 ㉗ 项会查）。')
        return 2
    return 0


def print_plan(plan: dict) -> None:
    W = 78
    print('=' * W)
    print('社媒采集计划　%s（%d 天 · %s）' % (plan['destination'], plan['days'], plan['month']))
    print('=' * W)
    print('\n可用渠道（按证据上限降序，只列本机 READY 的）')
    for i, c in enumerate(plan['channels'], 1):
        print('  %d. [%s] %-38s 上限 %s' % (i, c['tier'], c['name'], c['ceiling']))
    print('\n关键词组（用搜索引擎，命中页读全文——**不读摘要**）')
    for q in plan['queries']:
        print('  %-4s %s' % (q['kind'], q['query']))
    b = plan['budget']
    print('\n预算：检索 ≤ %d 次 ｜ 每词取页 ≤ %d' % (b['search_calls'], b['fetch_pages_per_query']))
    print('       %s' % b['note'])
    print('\n死线（一律不得从社媒取）：%s' % ' / '.join(plan['deadlines']))
    print('\n产出：线索卡 JSON（跑 social_notes.py 校验）→ 写进事实源 meta.social_intel')


def main() -> int:
    ap = argparse.ArgumentParser(
        description='社媒采集渠道能力矩阵与采集计划（阶段 3.5）。')
    ap.add_argument('--doctor', action='store_true', help='打印能力矩阵（本机实测状态）')
    ap.add_argument('--live', action='store_true', help='附带网络连通性探测')
    ap.add_argument('--plan', metavar='目的地', help='生成该目的地的采集计划')
    ap.add_argument('--days', type=int, default=3, help='行程天数（影响检索预算）')
    ap.add_argument('--month', default='', help='出行月份，用于档期类关键词')
    ap.add_argument('--json', action='store_true', help='结构化输出')
    args = ap.parse_args()

    probe = _probe_config()
    rows = resolve(probe)
    live = _probe_live() if args.live else None

    if args.json:
        out = {'probe': probe, 'providers': rows, 'live': live}
        if args.plan:
            out['plan'] = build_plan(args.plan, args.days, args.month or '本月', rows)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if any(r['status'] == 'READY' for r in rows) else 2

    rc = 0
    if args.plan:
        print_plan(build_plan(args.plan, args.days, args.month or '本月', rows))
        print()
    if args.doctor or not args.plan:
        rc = print_doctor(rows, probe, live)
    return rc


if __name__ == '__main__':
    sys.exit(main())
