# 03 · 产品、流程、交互与文案设计

> 本文所有客户可见文案均为**定稿原文**(中英双语)。Sonnet 实施时把它们写入 `copy` 配置的默认值(05 §4),不得自行改写;需改动请走配置中心。
> 文案中 `{x}` 为占位符;`[[icon]]` 为现有图标占位(`ui/icons.py`);按钮写作 `[文字]`。
> 文案原则:**像一位懂行、礼貌、说话算话的店员**;一卡一事;先给结论;告诉客户接下来会发生什么、需要/不需要他做什么;不用"亲""哦~";不用感叹号堆砌;英文用自然的商务口语,不直译。

---

## 1. 设计原则(每条都能追溯到 02 的场景)

1. **先答后问**(S-01/02/07/23):客户的首句是问题,就先回答;只有回答不了时才问。
2. **规则优先,模型兜底**(S-24):价目、入口、核验、问答、订单状态全部零模型;模型只负责"需要理解和组织语言"的部分。
3. **每题都能跳过,每步都能被打断**(S-04/10):不设关卡式流程;客户说别的,先回应再回来。
4. **真人可达,但不廉价**(D1/D2):不放"找人"按钮;客户要人或命中升级矩阵即转交。
5. **事实先行**(S-15/20):涉及钱和信任时,先说可核验的事实(订单号、金额、时间、白名单),再说安抚的话。
6. **一个会话,一个焦点卡**(M1/M2):问答用一张"进度卡"原位演进;订单用一张"订单卡"原位更新并置顶;旧卡收起或作废。
7. **Business 是业主的私人客厅**(S-06/07):自然语言优先,不发菜单、不放特效、不置顶、主动披露 AI 身份。
8. **克制**:特效只在两种时刻;反应只在 Bot 私聊;任何"仪式感"都不能出现在付款异常与争议中。

---

## 2. 状态机

### 2.1 Case 状态(沿用现有 `frontdesk_case.state` 取值,新增 2 个)

```
              ┌──────────── 任意时刻:升级矩阵命中 / 客户要真人(D1) ─────────────┐
              │                                                                ▼
  (无case) ─entry─▶ FRONTDESK ─选择/识别─▶ PRECHECK(=intake) ─完成─▶ AI_SALES / AI_SUPPORT ─▶ HUMAN_PENDING ─接管─▶ HUMAN_ACTIVE
      │               │  ▲                     │ ▲                        │   ▲   出口:请求报价             │                 │
      │       先答卡片 │  │回访续接             │ │断点续接                 │   │                            │ 管理员"恢复AI"   │释放/结案
      │               ▼  │                     ▼ │                        ▼   │                            ▼                 ▼
      └──────────▶ ANSWERED ◀──────────── (客户点了价目/入口后离开) ─────── QUOTE_PENDING(新) ──报价发出──▶ (订单状态接管)   RESOLVED
                   (新:已先答、尚未进入问答)                                                                               │
                                                                                                    7 天无互动 ─▶ DORMANT(新,仅统计用)
```

- `ANSWERED`(新):客户得到了先答卡(价目/入口/核验/FAQ),但还没进入问答或 AI。下一条消息按"已有上下文"处理:例如看过软件价目卡后发"帮我选" → 直接进软件问答,不再问来意。
- `QUOTE_PENDING`(新):AI 或问答已收齐报价所需信息,等待同事出报价。它是 `HUMAN_PENDING` 的一种,在队列中显示为"待报价",优先级 P1。实现上可用 `state=HUMAN_PENDING` + `handoff_reason=quote_request`,不必新增枚举值(由 Sonnet 选择,05 §3.2 推荐后者以减少迁移)。
- `DORMANT`:不在数据库中改状态,只在统计查询中派生(last_customer_at > 7 天)。

### 2.2 人工锁(覆盖层,现有)
`HUMAN_PENDING` / `HUMAN_ACTIVE` 时 AI 不回复;客户消息转发管理群。**新增**:`HUMAN_PENDING` 期间客户发消息或 /start,回一张"处理中"卡(§4.3),每 30 分钟最多一次,避免客户以为没人收到。

### 2.3 订单状态(现有 `whitecat_core/payments/orders.py`)与客户看到的卡片
| 订单状态 | 订单卡(置顶,原位更新)标题 | 客户需做什么 |
|---|---|---|
| quoted/awaiting_payment | 待付款 · 订单 {id} | 按卡片付款,或提交交易哈希 |
| payment_review | 正在核对付款 | 不需要做什么 |
| paid | 已收到付款 🎉(effect) | 不需要做什么 |
| fulfilling | 正在交付 | 等待/配合同事 |
| delivered | 已交付 🎉(effect) | 验收 |
| cancelled/superseded | 订单已关闭 / 已被新报价替代 | — |
| refunded/disputed | 已退款 / 争议处理中 | — |

---

## 3. 入口

### 3.1 入口分类器(规则,零模型)

对每条**新 case 的首条消息**、以及 `FRONTDESK/ANSWERED` 状态下的消息运行。按顺序匹配,命中即停:

| 顺序 | 意图 | 触发(示例关键词/规则,完整词表在配置 `entry.intents`) | 动作 |
|---|---|---|---|
| 1 | `verify_request` | 含 `@用户名`、TRON/TON/EVM 地址、或"是你们/是不是官方/真的假的/冒充/is this you/official" | 真伪核验卡(§4.8) |
| 2 | `payment_issue` | "付了/已付款/转了/没到账/没收到/到账/paid/sent the money" 且 (有未结订单 或 含"没") | 升级矩阵 P0(§6),如有订单先给订单状态 |
| 3 | `refund` | "退款/退钱/refund/chargeback" | 升级矩阵 P0 |
| 4 | `angry` | "骗子/跑路/投诉/垃圾/scam/fraud" | 有已付订单 → P0;否则 → 安抚卡 + 进入正常路径(不转人) |
| 5 | `human_request` | "人工/真人/客服/老板/主理人/human/real person/agent/owner" | D1 流程(§4.4) |
| 6 | `is_bot` | "你是机器人/是AI吗/真人吗/are you a bot/are you human" | 身份直答卡 |
| 7 | `sensitive` | 敏感业务线关键词(KYC/实名/认证/号池/协议号/tdata 等,取自业务线 `sensitive=true` 的 keywords) | 登记卡(§4.9) |
| 8 | `support_*` | 现有售后词表 + "装不上/打不开/报错/激活/卡密/无效/error/crash/activate/license" | 售后问答(§5.3) |
| 9 | `partner` | 现有合作词表 + "底价/拿货/分销/代理价" | 合作问答 |
| 10 | `price_ask` | "多少钱/价格/报价/怎么卖/便宜/优惠/price/how much/cost" | 价目卡(按已识别业务线或来源;都没有则总览价目卡) |
| 11 | `buy_now` | "买/购买/下单/开通/续费" + 业务线=software | 官方入口卡 |
| 12 | `line_question` | 命中某业务线关键词且句子含问号/疑问词/≥10 字 | 该业务线"极简确认 + AI"(若该线 `intake.answer_first=true`)或进入该线问答 |
| 13 | `browse` | "做什么的/有哪些/介绍/what do you do/services" | 业务总览卡 |
| 14 | `greeting` | 仅问候(≤6 字,"你好/在吗/hi/hello") | Bot 私聊:欢迎卡;Business:自然问候(§4.6) |
| 15 | `unknown` | 其他 | Bot 私聊:欢迎卡(原话暂存);Business:自然问候 |

- 同一句话命中多个意图(例如"付了钱激活不了"):按上表顺序取第一个,但**抽取器**会把其他信号都记入简报。
- **来源感知**:`/start src_web_tg`(软件官网)、`src_web_main`(主站)、`src_channel`、`src_group`、`src_app`(软件内)、`ord_<id>`(订单深链)、`buy_<line>`。来源写入 case,并影响:欢迎卡按钮顺序(软件官网/软件内来源 → 软件在前)、`price_ask` 无业务线时默认展示的价目卡。
- 词表与动作在配置中可编辑;顺序 1–5 的**动作**锁定为代码行为(只能增删关键词)。

### 3.2 欢迎卡(Bot 私聊)

**zh**
```
[[robot]] <b>欢迎来到白猫工作室</b>

想了解什么,直接说就行;也可以点下面的按钮。
价格、功能、售后问题,我会先帮您找到答案。

<i>官网 tg.web3baimao.com · 白猫不会向您索要密码、验证码或助记词</i>
```
按钮(每行一个;顺序受来源影响,下为默认):
- `[🛒 买软件 / 看价格]` → callback `fd:go:software`
- `[🤖 定制机器人]` → `fd:go:bot_custom`
- `[🌐 静态 IP · AI 账号 · 其他]` → `fd:menu:more`(展开二级:静态 IP / AI 账号 / 其他业务)
- `[🛠 已购买 · 售后]` → `fd:role:support`
- `[🤝 渠道合作]` → `fd:role:partner`

**en**
```
[[robot]] <b>Welcome to WhiteCat Studio</b>

Just tell me what you're looking for, or tap a button below.
Pricing, features or an issue with an order — I'll get you an answer first.

<i>Website tg.web3baimao.com · We'll never ask for your password, login code or seed phrase</i>
```
- `[🛒 Software & pricing]` `[🤖 Custom bots]` `[🌐 Static IPs · AI accounts · More]` `[🛠 I'm a customer — support]` `[🤝 Partnerships]`

> 改动理由:原四选一"了解产品/售后/合作/先看看"让客户做一次**无信息量的身份选择**;新卡直接给业务入口(选了就进入该业务的"先答"),一次点击替代两次。"欢迎来到白猫工作室"保留为标题(业主原话)。

### 3.3 回访卡

**≤24 小时且 case 未结束**:不发卡,直接续接(§5.6)。

**24 小时–7 天(`welcome_back_recent`)**
- zh:`[[robot]] <b>欢迎回来</b>\n上次聊到:{summary}\n要接着上次的继续吗?`
  按钮:`[继续上次的]` `[这次是新问题]`
- en:`[[robot]] <b>Welcome back</b>\nLast time we talked about: {summary}\nShall we pick up where we left off?`
  按钮:`[Continue]` `[Something new]`

**>7 天(`welcome_back_old`)**:欢迎卡正文前插入一行 zh:`上次您咨询过:{summary}` / en:`Last time you asked about: {summary}`。

`{summary}` = 业务线名 + 最多 30 字需求摘要,例如"定制机器人 · 自动回复客户(1 个月内)"。

### 3.4 待跟进中的回访(`pending_status`)
- zh:`[[clock]] <b>您的问题已在处理中</b>\n{reason_line}已等待 {wait}。同事会在这里回复您,不需要重复发送。\n有新情况可以直接补充。`
  按钮:`[这是另一个新问题]` → 新建 case(旧 case 留在队列)。
- en:`[[clock]] <b>We're on it</b>\n{reason_line}Waiting time so far: {wait}. A teammate will reply right here — no need to resend.\nFeel free to add any new details.`
  按钮:`[This is a different question]`
- `{reason_line}` 例:zh "关于:付款核对。" en "About: payment check. "
- 频率:同一 case 每 30 分钟最多一次;其间的消息静默转发管理群 + Bot 私聊中设置 ✍ 反应(§8.5)。

---

## 4. 卡片与文案全集

### 4.1 价目卡

**软件价目卡 `card.price_software`**(数字来自知识库 `products[id=software].plans`,规则渲染;下为当前数据渲染结果)
- zh:
```
[[package]] <b>WhiteCat 5.0 · 授权价格</b>
新设备自带免费试用,可以先用再买。

• 尝鲜体验卡 6.9 U · 3 天(每台设备限 1 次)
• 月度旗舰版 29 U · 30 天
• 季度进阶版 69 U · 90 天
• 年度专业版 199 U · 365 天
• 创世合伙人·终身版 399 U · 永久 · 3 台设备

以上为单设备价格,以软件内购买页实时显示为准。
```
  按钮:`[🛒 官方购买入口]`(`fd:cta:official_buy`) `[🤔 帮我选套餐]`(`fd:go:software_pick`) `[🧩 了解其他服务]`(`fd:menu:more`)
- en:
```
[[package]] <b>WhiteCat 5.0 · License pricing</b>
New devices come with a free trial, so you can try before you buy.

• Starter pass 6.9 U · 3 days (once per device)
• Monthly 29 U · 30 days
• Quarterly 69 U · 90 days
• Annual 199 U · 365 days
• Founding Partner · Lifetime 399 U · forever · 3 devices

Prices are per device; the in-app checkout always shows the live price.
```
  按钮:`[🛒 Official checkout]` `[🤔 Help me choose]` `[🧩 Other services]`

> 套餐英文名是**翻译建议**,业主可在配置中改;知识库新增 `name_en` 字段。

**业务总览价目卡 `card.price_overview`**
- zh:
```
[[package]] <b>白猫工作室 · 服务与参考价</b>

• WhiteCat 5.0 获客软件 —— 6.9 U 起,新设备可免费试用
• Telegram 机器人定制 —— 288 U 起,按功能报价
• 独享静态 IP —— 0.43 U/个起,量大价优
• AI 大模型账号 —— ChatGPT Plus 参考价 130 元

以上为公开参考价,成交以购买页或报价单为准。想了解哪一项?
```
  按钮:`[软件]` `[定制]` `[静态 IP]` `[AI 账号]` `[其他]`(两列)
- en:
```
[[package]] <b>WhiteCat Studio · Services & reference prices</b>

• WhiteCat 5.0 growth software — from 6.9 U, free trial on new devices
• Custom Telegram bots — from 288 U, quoted by scope
• Dedicated static IPs — from 0.43 U each, better rates in bulk
• AI model accounts — ChatGPT Plus from 130 CNY (reference)

These are public reference prices; the final price is on the checkout page or your quote. Which one interests you?
```
  按钮:`[Software]` `[Custom bot]` `[Static IP]` `[AI accounts]` `[Other]`

### 4.2 官方购买入口卡 `card.official_buy`
- zh:
```
[[check]] <b>官方购买入口</b>
两种方式都会自动发放卡密并激活:

1. 在软件内打开「购买」页,一键下单(推荐)
2. 官方购买机器人 @oxbaimaobot

买完遇到任何问题,回到这里发订单号或卡密前 4 位即可。
```
  按钮:`[打开 @oxbaimaobot]`(url `https://t.me/oxbaimaobot`) `[下载软件]`(url = 知识库 `official.releases`) `[还有问题想问]`(`fd:go:software_ai`)
- en:
```
[[check]] <b>Official checkout</b>
Both options deliver and activate your license key automatically:

1. Open the Purchase page inside the app (recommended)
2. Our official purchase bot @oxbaimaobot

If anything goes wrong after buying, come back here with your order number or the first 4 characters of your key.
```
  按钮:`[Open @oxbaimaobot]` `[Download the app]` `[I have a question]`

### 4.3 帮我选套餐 `q.software.horizon` + 推荐卡
- 问题 zh:`[[question]] <b>打算用多久?</b>\n不确定也没关系,选个大概。`;en:`[[question]] <b>How long do you expect to use it?</b>\nA rough guess is fine.`
- 选项:`先试几天 / Just trying it` · `1 个月 / About a month` · `3 个月 / About 3 months` · `1 年以上 / A year or more` · `长期 + 多台设备 / Long-term, several devices`
- 推荐规则(配置 `playbook.software.recommend`):trying→尝鲜体验卡(并提醒免费试用);1m→月度;3m→季度;1y→年度;multi→终身(3 台)或"台数×年度"对比。
- 推荐卡 zh:`[[sparkles]] <b>建议:{plan}({price} U · {duration})</b>\n{reason}\n\n{compare}` en:`[[sparkles]] <b>Our pick: {plan} ({price} U · {duration})</b>\n{reason}\n\n{compare}`
  - `{reason}` 示例 zh:"按 1 个月的使用时间,月度最划算;如果确定会长期用,年度平均每月不到 17 U。" en:"For about a month, Monthly is the best value; if you'll stay longer, Annual works out under 17 U a month."
  - `{compare}`(多设备):zh "两台设备:2 × 年度 = 398 U/年;终身版 399 U 含 3 台设备。" en "Two devices: 2 × Annual = 398 U/year; Lifetime is 399 U and covers 3 devices."
  - 计算由代码完成(知识库数字),金额字符串因来自知识库可通过护栏。
  - 按钮:`[🛒 官方购买入口]` `[💬 问问细节]`

### 4.4 要求真人(D1)
- 询问 `human.ask` zh:`[[chat]] 好的。用一句话说说您要办的事,我连同记录一起交给同事。`;en:`[[chat]] Sure. Tell me in one sentence what you need, and I'll pass it to a teammate along with our chat.`
- 已转交 `human.done` zh:`[[check]] <b>已交给同事</b>\n{eta}\n这里的记录对方都能看到,不需要重复发送。`;en:`[[check]] <b>Passed to a teammate</b>\n{eta}\nThey can see everything here, so there's no need to repeat yourself.`
- `{eta}`(由 SLA 配置与当前时间计算,§6.2):
  - 工作时间 zh "通常 {n} 分钟内在这里回复您。" en "They usually reply here within {n} minutes."
  - 夜间 zh "现在是夜间,同事会在上午 {h} 点前回复您。" en "It's night here; they'll reply by {h} AM (UTC+8)."
- 若客户在 `human.ask` 后又说"算了/不用了" → 取消,回到原状态,zh "好的,有需要随时说。" en "No problem — just message me anytime."

### 4.5 身份直答 `card.is_bot`
- zh:`我是白猫工作室的 AI 助理。简单的问题我可以马上回答;需要同事处理的,说一句您的事,我会直接转给他们。这里的对话同事都能看到。`
- en:`I'm WhiteCat Studio's AI assistant. I can answer the quick questions right away; for anything that needs a teammate, just tell me what it's about and I'll pass it on. Our team can see this chat.`

### 4.6 Business 专用
- 首条披露前缀 `biz.disclosure`(加在机器人在该 Business 会话发出的第一条消息开头,单独一行):
  zh:`(白猫工作室 AI 助理代为接待,老板也会看到这段对话)`
  en:`(This is WhiteCat Studio's AI assistant, replying on the owner's behalf — they can see this chat too.)`
- 问候 `biz.greeting_known`(有历史 case/订单)zh:`在的~有什么需要直接说,我先帮您看。`;en:`Hi! Tell me what you need and I'll take a look first.`
- 问候 `biz.greeting_new` zh:`您好,这里是白猫工作室。想了解哪方面?软件、机器人定制、静态 IP 或其他,直接说就好。`;en:`Hi, this is WhiteCat Studio. What can we help with — software, custom bots, static IPs or something else? Just tell me.`
- 绑定 Bot 私聊 `biz.bind_bot`(报价/订单时)zh:`[[link]] 订单和到账通知会发在白猫官方机器人里,方便您随时查看。点下面按钮打开即可(只需一次)。` 按钮 `[打开订单]`(url `https://t.me/{bot_username}?start=ord_{id}`);en:`[[link]] Your order and payment updates will be sent through our official bot so you can check them anytime. Tap below to open it (one-time).` 按钮 `[Open my order]`

### 4.7 进度卡(问答)通用结构
```
{icon} <b>{question}</b>
{hint}

{progress}
```
- `{progress}` = `● ● ○`(已答/当前/未答,最多 3 点;只有 1 题时不显示)。
- 每题下方按钮 + `[跳过]`(可跳过的题)/ zh "跳过" en "Skip"。
- 答完后进度卡**原位**变为下一题;全部完成后变为**确认卡**:
  - zh:`[[note]] <b>我理解的对吗?</b>\n{lines}`;按钮 `[✅ 对,继续]` `[✏️ 改一下]`
  - en:`[[note]] <b>Did I get this right?</b>\n{lines}`;按钮 `[✅ Yes, continue]` `[✏️ Let me fix something]`
  - `{lines}` 每行 `• {label}:{value}`。
  - "改一下" → 列出各项按钮,点哪项重问哪项。
- 确认后进度卡**收起**为:zh `✓ 需求已记下:{line_name} · {need_short}` / en `✓ Noted: {line_name} · {need_short}`(无按钮)。
- 断点续接前缀 zh `接着刚才的:` en `Picking up where we left off: `。

### 4.8 真伪核验
- 账号是官方 `verify.account_ok` zh:`[[check]] <b>@{u} 是白猫官方账号</b>({role})。` en:`[[check]] <b>@{u} is an official WhiteCat account</b> ({role}).`
- 账号不是官方 `verify.account_bad` zh:`[[alert]] <b>@{u} 不是白猫官方账号,请不要向它付款。</b>` en:`[[alert]] <b>@{u} is not an official WhiteCat account. Please don't send it any money.</b>`
- 地址是官方 `verify.addr_ok` zh:`[[check]] 这个地址是白猫的收款地址({network},尾号 {tail})。付款前请确认它出现在本机器人发给您的、带订单号的订单卡里。` en:`[[check]] This is a WhiteCat payment address ({network}, ending {tail}). Before paying, make sure it appears on an order card with an order number sent by this bot.`
- 地址不是官方 `verify.addr_bad` zh:`[[alert]] <b>这不是白猫的收款地址。</b>白猫只在本机器人发出的、带订单号的订单卡里提供收款地址。` en:`[[alert]] <b>This is not a WhiteCat payment address.</b> We only share payment addresses on order cards with an order number, sent by this bot.`
- 官方列表 `verify.list` zh:`白猫官方账号:\n{list}\n官网:tg.web3baimao.com · web3baimao.com` en 同结构 `Official WhiteCat accounts:\n{list}\nWebsites: ...`
- 已被骗建议 `verify.scammed` zh:`如果已经转账,请保留转账记录,并在对方资料页点「举报」。第三方收走的款项我们无法追回,但会把这个账号记录下来提醒其他客户。` en:`If you've already paid, keep the transaction record and report the account from its profile. We can't recover funds sent to third parties, but we'll log this account to warn others.`
- 订单查询 `/order {id}`:存在 → 订单卡(只读副本,地址显示前 4 后 6);不存在 zh `[[alert]] 没有找到订单 {id}。白猫的订单号由本机器人生成,如果有人发给您一个查不到的订单号,请提高警惕。` en `[[alert]] We couldn't find order {id}. Our order numbers are generated by this bot — if someone gave you one that doesn't exist here, please be careful.`

### 4.9 敏感业务登记 `card.sensitive`
- zh:`[[note]] <b>{line_name}</b>\n这类业务需要同事评估后才能确认能否承接。请用一句话说明目标平台和需求,同事会在这里回复您。\n白猫不会索要您的账户密码、验证码或助记词。`
- en:`[[note]] <b>{line_name}</b>\nA teammate needs to review requests like this before we can confirm. Tell me the platform and what you need in one sentence, and they'll reply here.\nWe'll never ask for your account password, login code or seed phrase.`
- 收到后 zh:`[[check]] 已登记,同事评估后会在这里回复您。` en:`[[check]] Got it. A teammate will review and reply here.`
- 业务线名称(D6 默认):zh "平台认证咨询" / "账号类咨询";en "Platform verification enquiry" / "Account enquiry"。

### 4.10 售后
- 已知问题卡 `support.known.install_win` zh:
```
[[info]] <b>Windows 提示"已保护你的电脑"?</b>
软件暂未购买代码签名,首次运行可能被拦截,这是正常的:
1. 点击「更多信息」
2. 点击「仍要运行」
完整图文步骤见安装指南。
```
  按钮 `[安装指南]`(url 知识库 `official.install_guide`) `[✅ 解决了]` `[❌ 还是不行]`
  en:
```
[[info]] <b>Windows says "Windows protected your PC"?</b>
The app isn't code-signed yet, so the first launch may be blocked. That's expected:
1. Click "More info"
2. Click "Run anyway"
See the install guide for the full walkthrough.
```
  按钮 `[Install guide]` `[✅ That fixed it]` `[❌ Still not working]`
- macOS 版 `support.known.install_mac` zh:`macOS 提示"无法打开,因为无法验证开发者"?打开「系统设置 → 隐私与安全性」,在下方找到被拦截的 WhiteCat,点「仍要打开」。` en:`macOS says the app "can't be opened because the developer cannot be verified"? Open System Settings → Privacy & Security, find WhiteCat near the bottom and click "Open Anyway".`【假设:按 macOS 13+ 的通用流程撰写,需业主对照官方安装指南确认】
- 解决后收起 zh `✓ 已解决` en `✓ Resolved`。
- 卡密问题询问 `support.license.ask` zh:`[[key]] 请发一下卡密的<b>前 4 位</b>和大概的购买时间,我交给负责授权的同事核对。\n请不要把完整卡密发给任何人,包括自称白猫客服的人。` en:`[[key]] Please send the <b>first 4 characters</b> of your key and roughly when you bought it. I'll pass it to the teammate who handles licenses.\nDon't share your full key with anyone — including people claiming to be WhiteCat support.`(若 `key` 图标不存在,用 `lock`)
- 退款 `support.refund` zh:`[[note]] 退款需要同事核对订单和使用情况。已为您登记,同事会在这里联系您。{eta}` en:`[[note]] Refunds need a teammate to check the order and usage. I've logged your request and they'll contact you here. {eta}`

### 4.11 愤怒的已付款客户 `care.angry_paid`
- zh:`非常理解您的着急。您的付款已确认到账(订单 {id},{amount} USDT,{paid_at} 到账),已加急通知负责同事。{eta}`
- en:`I completely understand. Your payment is confirmed (order {id}, {amount} USDT, received {paid_at}), and I've flagged it as urgent for the team. {eta}`
- 未付款/无订单的愤怒 `care.angry` zh:`抱歉让您不愉快。您可以直接说想解决的问题,我尽量马上帮您处理;需要同事的话也可以直接说。` en:`Sorry this has been frustrating. Tell me what you need sorted and I'll do my best right away — or just say if you'd like a teammate.`

### 4.12 降级文案(更新现有 `texts.py` 键)
- `offline` zh:`[[alert]] 我这边暂时没能生成回复。您的问题已记下并转给同事,不需要重复发送。` en:`[[alert]] I couldn't put a reply together just now. Your question is saved and passed to a teammate — no need to resend.`
- `quota_ended` zh:`[[clock]] 这次我能帮的部分差不多了,接下来交给同事继续。我们的对话已保存,同事会在这里回复您。` en:`[[clock]] I've covered what I can for now — a teammate will take it from here. Our chat is saved and they'll reply right here.`
- `needs_human` zh:`[[chat]] 这一点涉及具体价格或付款,为避免说错,我请同事来确认。问题已保存,不需要重复发送。` en:`[[chat]] This involves exact pricing or payment, so I've asked a teammate to confirm rather than guess. Your question is saved — no need to resend.`
- `thinking`(Business 无草稿时不发;Bot 私聊用 sendMessageDraft 空文本占位,客户端显示 "Thinking…",无需文案)
- 语音 `voice.reply` zh:`抱歉,我暂时听不了语音 🙏 方便打几个字吗?` en:`Sorry, I can't listen to voice messages yet 🙏 Could you type it out?`

### 4.13 报价与付款(WP8)
- 报价卡 `order.quote`:
  - zh:
```
[[payment]] <b>报价单 · {id}</b>
{title}
{detail}

金额:<b>{amount} USDT</b>
网络:<b>{network}</b>(只支持此网络,转错网络无法到账)
收款地址:<code>{address}</code>
地址核对:{head}…{tail}
有效期至:{expires_local}

请确保<b>到账金额</b>为 {amount} USDT(交易所手续费请另付)。
付款后会自动确认,一般 1–5 分钟;也可以点下方按钮提交交易哈希加快核对。
```
  按钮:`[我已付款,提交交易哈希]`(`pay:txid:{id}`) `[有疑问,联系同事]`(`pay:help:{id}`)
  - en:
```
[[payment]] <b>Quote · {id}</b>
{title}
{detail}

Amount: <b>{amount} USDT</b>
Network: <b>{network}</b> (this network only — transfers on other networks won't arrive)
Address: <code>{address}</code>
Check: {head}…{tail}
Valid until: {expires_local}

Please make sure we <b>receive</b> exactly {amount} USDT (exchange fees are on the sender).
Payments are confirmed automatically, usually within 1–5 minutes. You can also submit the transaction hash below to speed things up.
```
  按钮 `[I've paid — submit tx hash]` `[Question? Contact a teammate]`
  - `{amount}` 为**唯一金额**(例如 288.07,§9.3);`{head}`/`{tail}` = 地址前 4、后 6 位。
- 请求 TxID `order.ask_txid` zh:`好的,请把交易哈希(TxID)粘贴发给我。在交易所的提币记录里可以复制到。` en:`Great — please paste the transaction hash (TxID). You'll find it in your exchange's withdrawal history.`
- 截图收到 `order.receipt` zh:`收到截图。为了自动核对到账,请把<b>交易哈希(TxID)</b>发给我;没有也没关系,系统也会按金额自动查找,通常 1–5 分钟。` en:`Got your screenshot. To match it automatically, please send the <b>transaction hash (TxID)</b>. If you don't have it, that's fine — we also look it up by amount, usually within 1–5 minutes.`
- 核验中 `order.checking` zh:`[[clock]] 正在核对这笔交易…` en:`[[clock]] Checking this transaction…`(核验完成后**删除**这条临时消息,M1)
- 已收款 `order.paid`(带 🎉 effect,置顶订单卡更新)zh:`[[check]] <b>已收到您的付款</b>\n订单 {id} · {amount} USDT · {paid_at}\n{next_step}\n可以随时发送 /order {id} 查看进度。` en:`[[check]] <b>Payment received</b>\nOrder {id} · {amount} USDT · {paid_at}\n{next_step}\nSend /order {id} anytime to check progress.`
  - `{next_step}` 来自商品配置,例 zh "负责同事会在今天 18:00 前与您确认开工时间。" en "Your project lead will confirm the start date by 6 PM today (UTC+8)."
- 容差放行 `order.paid_tolerance` = `order.paid` + 一行 zh `(实收 {received} USDT,手续费差额已为您忽略)` en `(Received {received} USDT — we've waived the fee difference.)`
- 少付 `order.underpaid` zh:`[[info]] 已收到 {received} USDT,距订单金额还差 <b>{diff} USDT</b>。\n您可以把差额转到同一地址(同一网络),或联系同事处理。订单已为您保留。` 按钮 `[我补上了,提交交易哈希]` `[联系同事]`;en:`[[info]] We've received {received} USDT — <b>{diff} USDT</b> short of the order total.\nYou can send the difference to the same address (same network), or ask a teammate to help. Your order is on hold for you.` 按钮 `[I've topped up — submit tx hash]` `[Contact a teammate]`
- 多付/重复 `order.overpaid` zh:`[[info]] 我们收到了 {received} USDT,比订单金额多 {diff} USDT(或是重复付款)。同事会联系您处理退回或抵扣,不需要您做什么。` en:`[[info]] We received {received} USDT, {diff} USDT more than the order total (or a duplicate payment). A teammate will contact you about a refund or credit — nothing you need to do.`
- 未找到 `order.not_found` zh:`[[alert]] 在 {network} 网络上没有找到这笔交易。请确认提币时选择的网络是 {network};如果选了其他网络,请把交易哈希和网络名称发给我,同事会帮您核实。` en:`[[alert]] We couldn't find this transaction on {network}. Please check that you chose {network} when withdrawing. If you used another network, send me the tx hash and the network name and a teammate will look into it.`
- 交付完成 `order.delivered`(带 🎉 effect)zh:`[[party]] <b>订单 {id} 已交付</b>\n感谢信任白猫工作室。使用中有任何问题,直接在这里说。` en:`[[party]] <b>Order {id} delivered</b>\nThanks for choosing WhiteCat Studio. If anything comes up, just message us here.`
- 交付后回访(24h 后,一次)`order.checkin` zh:`[[smile]] 订单 {id} 用得还顺利吗?` 按钮 `[一切正常]` `[有问题]`;en:`[[smile]] Is everything working well with order {id}?` 按钮 `[All good]` `[I need help]`
- 到期提醒(本机器人售出、有到期日,提前 3 天,仅 Bot 私聊)`order.renew` zh:`[[clock]] {title} 将在 {date} 到期。需要续费的话,点下方按钮,我请同事为您出续费单。` 按钮 `[续费]` `[暂不需要]`;en:`[[clock]] {title} expires on {date}. Tap below if you'd like to renew and we'll prepare a renewal quote.` 按钮 `[Renew]` `[Not now]`

### 4.14 转交确认(按原因)
统一结构:`[[check]] <b>{title}</b>\n{body}\n{eta}`;`{eta}` 见 §6.2。
| reason | zh title / body | en title / body |
|---|---|---|
| quote_request | 已交给同事出报价 / 您的需求已整理好,同事会在这里给您一份报价单(金额、交期都会写清楚)。 | Passed on for a quote / Your requirements are ready; a teammate will send you a quote here with price and timeline. |
| large_deal | 已交给负责同事(优先处理) / 合同、发票等事项同事会直接与您确认。 | Passed to a senior teammate (priority) / They'll confirm contracts, invoices and the rest with you directly. |
| partner_intake | 合作意向已收到 / 负责渠道的同事会在这里联系您。可以继续补充介绍。 | Partnership request received / Our partnerships lead will contact you here. Feel free to add more details. |
| payment_issue | 付款问题已加急 / 财务同事会核对到账记录。 | Payment issue flagged as urgent / Our finance team will check the payment records. |
| paid_but_broken | 已交给负责授权的同事 / 我们会核对订单和卡密。 | Passed to our licensing team / We'll check your order and key. |
| explicit_human | (见 §4.4 human.done) | |
| sensitive_line | (见 §4.9) | |
| system_unavailable | (见 §4.12 offline) | |

> 注意:以上卡片出现在**转交之后**,不属于"预检阶段",可以出现"同事"。全部卡片不出现"人工"二字(满足业主字面要求且语气更自然)。

---

## 5. 问答(intake)引擎设计

### 5.1 概念
- **流程 flow**:绑定一个业务线(或售后/合作),由若干**槽位 slot** 组成。
- **槽位**:`{key, kind: choice|text|band, ask:{zh,en}, hint:{zh,en}, options:[{code, zh, en}], required: bool, skippable: bool, when: 条件表达式, extract: 抽取器名单}`。
- **先答卡**:流程可配置 `answer_first_card`(进入该线时先发的卡,例如定制说明卡)。
- **出口**:`exit: ai | handoff:<reason> | cta:<card>`。
- **最多出示 3 题**(配置 `intake.max_questions`,默认 3);已被抽取填充的槽位不出示;`required=false` 的题在已出示 2 题后自动省略。

### 5.2 业务线默认流程(写入 `intake.flows` 默认配置)

**software**(answer_first=价目卡;多数客户不进入问答)
| slot | kind | zh 问题 / 提示 | en | 选项 | 规则 |
|---|---|---|---|---|---|
| horizon | choice | 打算用多久? / 不确定也没关系,选个大概。 | How long do you expect to use it? / A rough guess is fine. | 见 §4.3 | 仅"帮我选套餐"时出示 |
| devices | choice | 准备在几台电脑上用? | How many computers will you use it on? | 1 台 / 2–3 台 / 4 台以上 | horizon=multi 或抽取到多设备时跳过 |
| os | choice | 用的是哪个系统? | Which operating system? | Windows / macOS(Apple 芯片)/ macOS(Intel) | 仅售后安装类出示 |
出口:推荐卡 → `ai`(客户继续问时)。

**bot_custom**(answer_first=定制说明卡 `card.custom_intro`)
`card.custom_intro` zh:
```
[[robot]] <b>Telegram 机器人定制</b>
按功能复杂度报价,<b>288 U 起</b>;谈好的价格不会随意上涨。
常见类型:AI 双向客服、发卡商城、监听引流、链上交易机器人、业务自动化脚本。
流程:说需求 → 出报价单 → 开工交付 → 验收售后。

为了给您一个靠谱的方向,我问 2–3 个小问题,都可以跳过。
```
en:
```
[[robot]] <b>Custom Telegram bots</b>
Priced by scope, <b>from 288 U</b> — and once we agree a price, it stays put.
Popular builds: AI customer-service bots, storefront bots, lead-capture listeners, on-chain trading bots and workflow automation.
How it works: tell us what you need → get a written quote → we build → you review, with after-sales support.

A few quick questions (all optional) so we can point you in the right direction.
```
| slot | kind | zh | en | 选项 / 规则 |
|---|---|---|---|---|
| need | text | 想让机器人帮您做什么?一句话就够。 | What should the bot do for you? One sentence is plenty. | 首句 ≥10 字且命中本线 → 视为已填 |
| key_feature | text | 最重要的一个功能是什么? | What's the single most important feature? | 可跳过;need 中已含"能/要/可以…并且…"多个动作时抽取第一个 |
| deadline | choice | 希望什么时候用上? | When do you need it by? | 1 周内 / 1 个月内 / 不着急 —— Within a week / Within a month / No rush;抽取"急/马上/这周/today/asap" → 1 周内 |
| budget | band | 预算大概在哪个区间?\n同样的功能有轻量版和完整版做法,知道区间能给您更合适的方案。不方便说可以跳过,不影响报价。 | Roughly what budget do you have in mind?\nMost features can be built lean or full-featured — a range helps us suggest the right one. Feel free to skip; it won't affect your quote. | 300 U 以内 / 300–800 U / 800–2000 U / 2000 U 以上 / 先不说 —— Under 300 U / 300–800 U / 800–2,000 U / Over 2,000 U / Rather not say;deadline=1 周内且客户语气急 → 不出示;已抽取到金额 → 不出示 |
出口:确认卡 → `ai`;若 budget=2000+ 或抽取金额 ≥2000 → `handoff:large_deal`。
- 低预算卡 `card.custom_low_budget`(budget=<300 时,确认卡前)zh:`[[info]] 定制起步价是 288 U,300 U 以内通常能做单一功能的轻量机器人。如果主要是自动回复咨询,也可以先看看我们的现成方案,我接下来帮您对比一下。` en:`[[info]] Custom builds start at 288 U, and under 300 U usually covers a lean, single-purpose bot. If it's mainly answering enquiries, a ready-made option may fit too — I'll compare them for you next.`

**network**(answer_first=`card.network_intro` zh:`[[globe]] <b>独享静态 IP</b>\n独享住宅 / 机房 IP,公开参考价 <b>0.43 U/个起</b>(量大价优),到期前 30 天提醒续费。库存与实时价格由同事确认。` en:`[[globe]] <b>Dedicated static IPs</b>\nResidential or datacenter, from <b>0.43 U each</b> (better rates in bulk), with a renewal reminder 30 days before expiry. A teammate confirms live stock and pricing.`)
| slot | kind | zh | en | 选项 |
|---|---|---|---|---|
| ip_type | choice | 需要哪种 IP? | Which type of IP? | 住宅 / 机房 / 不确定 —— Residential / Datacenter / Not sure |
| qty | choice | 大概需要多少个? | Roughly how many? | 1–10 / 10–100 / 100 以上 —— 1–10 / 10–100 / 100+ |
| duration | choice | 使用多久? | For how long? | 1 个月 / 3 个月 / 更长 —— 1 month / 3 months / Longer |
出口:`handoff:quote_request`(库存与价格必须同事确认,AI 无法成交);qty=100+ 时 P1,否则 P2。若客户在问答后还有问题 → 可选 `ai`(配置 `network.allow_ai=true`)。

**ai_account**(answer_first=`card.ai_intro` zh:`[[sparkles]] <b>AI 大模型账号</b>\nChatGPT Plus 参考价 130 元,Gemini Pro 成品参考价 30 元;其他产品与库存由同事确认。白猫不会向您索要账号密码或验证码。` en:`[[sparkles]] <b>AI model accounts</b>\nChatGPT Plus from 130 CNY and Gemini Pro from 30 CNY (reference prices); a teammate confirms other products and stock. We'll never ask for your account password or login code.`)
| slot | kind | zh | en | 选项 |
|---|---|---|---|---|
| product | choice | 想要哪一款? | Which one are you after? | ChatGPT Plus / ChatGPT Pro / Gemini / Claude / 其他 |
| qty | choice | 需要几个? | How many? | 1 个 / 2–5 个 / 更多 |
出口:`handoff:quote_request`(P2)。

**support**(售后)
| slot | kind | zh | en | 选项 / 规则 |
|---|---|---|---|---|
| product | choice | 是哪一项产品或服务? | Which product or service is this about? | 业务线列表(不含敏感线)+ 其他;有历史 case/订单 → 预选 |
| issue | choice | 遇到的是哪类问题? | What kind of issue is it? | 安装与使用 / 授权与卡密 / 付款未到账 / 交付进度 / 续费升级 / 退款 —— Install & usage / License & key / Payment not received / Delivery status / Renew or upgrade / Refund |
| desc | text | 简单描述一下情况(报错文字可以直接粘贴)。 | Briefly describe what's happening (you can paste any error text). | install/renew → 可跳过 |
出口:issue=payment/refund → P0 handoff;license → `support.license.ask` → P0;install → 已知问题卡 → 未解决 → ai;delivery → 有订单则显示订单卡 + P2 handoff;renew → software:官方入口卡;其他:handoff P2。

**partner**:ptype(现有 5 项)→ scale(text,"大概的规模或资源,一句话")→ `handoff:partner_intake`(P1)。

**sensitive lines**(kyc / account):无问答,`card.sensitive` 一题 text → handoff P2。

### 5.3 文本输入的处理(修正现状"任何文字都当作当前题答案")
对 intake 中的每条客户文本:
1. 跑抽取器(§5.4),把能填的槽位都填上。
2. 若当前题已被填 → 推进。
3. 否则判断是否"在回答当前题":
   - choice 题:文本与选项名/同义词匹配 → 填;
   - text 题:文本长度 ≥2 且不是问句(不以 ?/? 结尾,不含"吗/呢/多少/怎么/how/what/can") → 填;
   - 否则视为**旁问**:若命中价目/FAQ/真伪等规则 → 先发对应卡/一句话答复;若是开放问题 → 一行 zh `这个问题我记下了,问完这一两个小问题后马上回答您。` en `Noted — I'll answer that right after one or two quick questions.` 并把问题存入简报 `pending_questions`(AI 首轮必须先回答它们);然后**重出当前题**(带"接着刚才的")。
4. 客户连续 2 次旁问 → 停止问答,直接进入出口(带已有信息),避免拉锯。

### 5.4 抽取器
**规则抽取器**(`extract_rules.py`,每条消息都跑,纯函数,可单测):
| 名称 | 输出 | 规则要点 |
|---|---|---|
| money | `{amount: float, currency: U/CNY/USD, raw}` | `(\d+(\.\d+)?)\s*(u|usdt|刀|美元|\$|元|块|k)`;"一千/两千/五千"中文数字;"500 左右/以内/上下"→ 区间 |
| qty | int | `(\d+)\s*(个|台|套|张|账号|号|ip)` |
| devices | int | `(\d+|两|三|四)\s*(台|部)?\s*(电脑|设备|机器|devices?)` |
| os | windows/macos_arm/macos_intel | win/windows/电脑(默认不判)/mac/m1–m4/苹果芯片/intel |
| horizon | trial/1m/3m/1y/multi | 试用/几天/一个月/月卡/季/年/长期/永久/终身 |
| urgency | urgent/normal | 急/马上/今天/尽快/asap/urgent |
| deadline | week/month/none | 这周/一周/本月/一个月/不急 |
| sentiment | angry/anxious/calm | 词表(骗子/垃圾/投诉 → angry;怎么还没/到底/着急 → anxious) |
| trust_concern | bool | 靠谱吗/真的假的/会不会跑路/legit/scam? |
| company | bool | 公司/团队/企业/我们老板/合同/发票/对公 |
| order_ref | str | 订单号格式 `[A-F0-9]{10}`、TxID 64 hex、卡密前 4 位(仅在 license 题后) |
| questions | int | 问号计数 + 疑问词分句 |
| reseller | bool | 代理/分销/渠道/拿货/底价 |

**模型抽取器**(`extract_llm.py`,可选,R-INTAKE-6):
- 触发:进入某业务线 intake 时,首条文本 ≥ 25 字,且规则填充后该流程必填槽位缺失 ≥1。
- 上限:每 case ≤ 1 次;每日全局 ≤ `intake.llm_extract.daily_cap`(默认 300);`max_tokens=300`;超时 6 秒;熔断打开或预算耗尽时不调用。
- 输入:流程的槽位定义(key + 选项 code)+ 客户原文;要求只输出 JSON。
- 校验:JSON 解析;choice 值必须在选项 code 中;band 必须在区间列表中;text ≤ 200 字;不合法字段丢弃。
- 结果只**填空**,不覆盖规则结果;所有模型填充的槽位在确认卡中出现,客户可改。
- 成本记入 `AiUsageLedger(event="extract")`,不占客户 AI 回复额度,但计入全局预算。

### 5.5 交接简报(HandoffBrief)
```json
{
  "v": 1,
  "case_id": 123, "channel": "bot|business", "lang": "zh", "source": "web_tg",
  "returning": false, "prior_orders": [{"id": "AB12…", "title": "…", "state": "delivered"}],
  "role": "prospect", "line": "bot_custom",
  "slots": {"need": "自动回复客户", "key_feature": "价格自动回复+转给我", "deadline": "month"},
  "slot_sources": {"need": "first_message", "key_feature": "text", "deadline": "button"},
  "budget": {"band": "300-800", "stated": null, "asked": true, "declined": false},
  "signals": {"urgency": "normal", "sentiment": "calm", "trust_concern": false, "company": false, "reseller": false, "competitor_compare": false},
  "pending_questions": ["大概多久能做好"],
  "first_message": "想做个能自动回复客户的机器人,大概多少钱?",
  "missing": ["channel_of_customers"],
  "escalation": null
}
```
- 存 `frontdesk_case.brief_json`;每次槽位变化更新;AI 每轮读取最新简报。
- 管理群/客户处理卡把简报渲染为中文要点(§04)。

### 5.6 续接与回访(D8)
- intake 中断后客户再来(≤24h):按 §5.3 处理新消息;若是非旁问的普通消息且当前题是 text → 当作答案。
- AI 阶段中断后再来(≤7 天):直接进入 AI 回合(不发卡);简报 `returning=true, gap_hours=n`,剧本要求 AI 首句自然衔接(例如"接着上次说的…")。
- 24h–7 天:§3.3 卡片;>7 天:欢迎卡带摘要。
- 有未结订单的客户回来:先发订单卡(只读副本),再处理消息。

---

## 6. 升级矩阵(R-HUMAN-1)

### 6.1 规则(配置 `escalation.rules`,有序,命中第一条)
| id | 条件 | 优先级 | 是否先问 1 题 | 客户卡 |
|---|---|---|---|---|
| payment_issue | 意图=payment_issue 或 issue=payment | P0 | 有未结订单:否;无订单:问"付款时间、金额和交易哈希(如有)" | payment_issue |
| refund | 意图=refund 或 issue=refund | P0 | 否 | support.refund |
| paid_but_broken | issue=license 或 (有已付订单 且 意图=support_*) | P0 | 是(卡密前 4 位/订单号) | paid_but_broken |
| angry_paid_customer | sentiment=angry 且 有已付订单 | P0 | 否 | care.angry_paid |
| impersonation_report | verify 结果为"非官方"且客户说已转账 | P1 | 否 | verify.scammed(不转交客户,仅通知管理群) |
| large_deal | 抽取金额 ≥ `escalation.large_deal_u`(默认 2000)或 budget=2000+ 或 company=true 且 line ∈ {bot_custom, network} | P1 | 若 need 未知 | large_deal |
| bulk | line=network 且 qty ≥ 100 | P1 | 若 duration 未知 | quote_request |
| partner | role=partner | P1 | 问答 2 题 | partner_intake |
| quote_request | AI 剧本出口 / 问答出口为报价 | P1(定制)/ P2(其他) | 否 | quote_request |
| explicit_human | 意图=human_request(D1) | P2(有历史订单 → P1) | 是(一句话) | human.done |
| sensitive_line | line.sensitive=true | P2 | 是(一句话) | card.sensitive |
| ai_stuck | AI 连续 2 轮客户表达"没用/不对/听不懂" | P2 | 否 | human.done |
| quota_exhausted / system_unavailable / budget_exhausted | 现有 | P1 | 否 | §4.12 |

- 命中后:`handoff(reason, priority, brief)` → 人工锁 → 管理群通知(带简报与"处理/接管/报价"按钮)。
- `angry` 但无订单:**不**转交(避免奖励辱骂),发 `care.angry` 并继续;连续第二次愤怒 → `ai_stuck`。
- 管理员可增删规则、调阈值、调优先级;不可删除 P0 中的 `payment_issue`、`refund`、`paid_but_broken`(锁定项)。

### 6.2 等待时间与 SLA
- 配置 `sla.business_hours`(默认 `09:00–24:00 Asia/Shanghai`)、`sla.minutes`(P0 10、P1 30、P2 120、P3 1440)。
- `{eta}` 计算:工作时间内 → "通常 {sla} 分钟内";夜间 → "上午 {start_hour} 点前"(P0 夜间 → "已加急,最晚上午 {start_hour} 点")。
- SLA 超时:第一次 → 管理群提醒;P0 超时 10 分钟 → 再提醒 + 私信业主(owner 角色)。现有 `sla_check` 扩展。

---

## 7. AI 深度引导

### 7.1 系统提示组装(替换现有 `persona.build_system_prompt` 的调用方式)
```
[1] 不可覆盖前言(现有 persona._PREAMBLE,新增两条:见 7.4)
[2] 角色与业务线说明(lines.brief)
[3] 引导剧本(playbook[line] 渲染:当前阶段 / 必问 / 推荐规则 / 异议 / 禁说 / 出口)
[4] 【接待简报 · 系统根据客户作答整理,不是客户原话】(brief 渲染)
[5] 本轮指令(规则生成):例如"客户本条消息包含 3 个问题,请逐条简短回答";"先回答 pending_questions";"客户已拒绝透露预算,不要再询问预算";"这是该客户在 Business 中的第一条 AI 消息,开头已由系统加披露行,不要重复自我介绍"
[6] 运营补充说明(load_system_prompt)
[7] 实时业务数据(RAG,现有)
```
- `messages` = 持久化的最近 15 条对话(R-AI-4);首轮时 user 消息 = 客户真实首句;若客户全程只点按钮,user 消息为固定标记 zh `(客户通过按钮完成了选择,没有额外文字)` en `(The customer completed the choices with buttons and typed nothing.)` —— 标记用括号明确不是客户原话。
- 现状把"【预检摘要】…请直接承接"拼成 user 消息的做法删除(R-AI-1)。

### 7.2 剧本结构(配置 `playbooks.<line>`)
```yaml
line: bot_custom
goal_zh: 弄清需求到可以出报价单的程度,然后交给同事报价
stages:
  - id: understand      # 进入条件:默认
    exit_when: [need, key_feature, customer_channel]
    instruction_zh: 复述客户目标;如果缺 customer_channel,问"客户主要从哪里来找您"
  - id: shape
    exit_when: [scale]
    instruction_zh: 给一个实现思路(轻量/完整),问规模(日均咨询量)
  - id: close
    action: handoff:quote_request
    say_zh: 这些信息够出一份报价单了,我现在交给负责定制的同事,他会在这里给您报价。
must_not_zh: [给出具体价格或交期, 承诺"最快3小时", 贬低同行, 再问已拒绝的预算]
objections:
  price_high_zh: 承认差异常见;说明报价由功能、稳定性和售后决定;可以先做核心功能分期交付;不承诺折扣
  competitor_zh: 不评价同行;建议客户比较交付物与售后条款
  trust_zh: 介绍公开承诺"谈好的价格不随意上涨"与报价单流程;可提醒发送 @用户名 让我核验官方账号
recommend_zh: []
max_turns_before_close: 4
```
- 阶段判断由**代码**做(看简报槽位是否已填),把当前阶段的 instruction 注入 [3];模型只负责语言。
- `exit_when` 中的槽位若在 AI 对话中被客户提到,由规则抽取器(每轮跑)写回简报。
- `action: handoff:*` 由代码在 AI 回复**之后**执行(AI 的 say 文本来自剧本,作为本轮指令要求 AI 以此收尾)。

默认剧本(写入配置默认值,中英各一份;下面只列要点,Sonnet 按 7.2 结构写全):
| line | goal | 阶段要点 | 出口 |
|---|---|---|---|
| software | 帮客户选对档位并去官方入口 | 回答功能问题(诚实边界)→ 了解用途/账号量 → 推荐 1 档 + 免费试用 | CTA `official_buy`(第 2 轮后附带一次) |
| bot_custom | 见上 | 见上 | handoff quote_request |
| network | 回答 IP 常识问题 | 类型差异、用途匹配 | handoff quote_request |
| ai_account | 回答品类问题 | 不涉及账号密码 | handoff quote_request |
| support(install/usage) | 一步一步排查 | 每轮只给一个步骤;2 轮未解决出口 | handoff ai_stuck |
| partner | 不进入 AI | — | — |

### 7.3 CTA 卡(R-AI-3)
- 规则:`cta.rules` 配置,例如 `{line: software, after_turn: 2, card: official_buy, once_per_case: true}`、`{line: software, when_slot: {stage: trial}, card: free_trial}`。
- CTA 在 AI 回复发送**之后**作为独立卡片发送(按钮为官方 URL),点击记 `cta_clicked`。AI 不生成链接(护栏也会拦)。

### 7.4 persona 前言新增条款(代码锁定)
- zh 8:`系统会提供【接待简报】。简报里已有的信息不要再问;客户拒绝透露的信息(例如预算)不要再问。`
- zh 9:`客户在一条消息里问了多个问题时,按顺序逐条简短回答(每条一行,最多 5 条),最后只问一个问题。`
- en 8:`The system provides an [Intake brief]. Never ask again for anything already in the brief, or for anything the customer declined to share (such as budget).`
- en 9:`If the customer asks several questions in one message, answer each briefly in order (one line each, max 5), then ask a single question.`

### 7.5 额度
现有额度保留(sales 12、support 6、partner 4;24h 滚动)。新增:`extract` 调用不计入客户额度;`cta`、规则卡片不计入。

---

## 8. 体验层(特效、置顶、清理、反应、草稿)

### 8.1 能力矩阵(依据 05 §6 的官方文档核实)
| 能力 | Bot 私聊 | Business 会话 | 管理群 | 默认 |
|---|---|---|---|---|
| 消息特效 `message_effect_id` | ✔(仅私聊) | 未证实【假设】 | ✘ | 私聊开;Business 关 |
| 置顶 `pinChatMessage` | ✔,无通知,产生服务消息 | 有 `business_connection_id` 参数,权限未写明【假设】 | 需管理员权限 | 私聊开;Business 关 |
| 删除 `deleteMessage` | 自己发的与客户发的,48h 内 | `deleteBusinessMessages` 需 `can_delete_sent_messages` | 需管理员 | 仅临时消息 |
| 反应 `setMessageReaction` | ✔(1 个,非付费) | ✘(无 business 参数) | ✔ | 私聊开 |
| 草稿 `sendMessageDraft` | ✔(私聊,30 秒临时预览) | ✘(无 business 参数) | ✘ | 私聊开 |
| 自定义表情(正文/按钮图标) | 需 Bot 所有者 Premium | 文档写"directly sent by the bot",Business 消息很可能不适用【假设】;现有代码失败降级 | 同私聊条件 | 保持现有降级 |
| 按钮颜色 `style` | ✔(9.4+) | 未写明限制 | ✔ | 现有 |
| Stars 发票 | ✔ | ✘(`sendInvoice` 无 business 参数) | — | 关(见 01 D10) |

### 8.2 特效(R-UX-3)
| 时刻 | effect | 通道 | 冷却 |
|---|---|---|---|
| `order.paid`(含容差放行) | 🎉 `5046509860389126442` | Bot 私聊 | 同一客户 24h 1 次 |
| `order.delivered` | 🎉 同上 | Bot 私聊 | 同上 |
- 不在以下场合使用:少付/多付/核对中/退款/争议/售后/问答完成/AI 回复/Business。
- 发送失败(`BadRequest` 含 effect)→ 去掉 effect 重发,并在 `capabilities` 缓存中标记 `effect=false` 24 小时。
- 配置:`experience.effects.enabled`、`.paid_effect_id`、`.delivered_effect_id`(管理员可换为 👍/❤️/🔥 等,需真机测试)。

### 8.3 置顶(R-UX-2)
- **置顶对象**:订单卡(每客户同时最多 1 张);售后 P0 工单卡(`support.license.ask` 等转交确认卡)在没有订单卡时可置顶(配置,默认关)。
- **时机**:订单进入 `awaiting_payment` 时发送订单卡并置顶;状态变化时**编辑同一条消息**(`ui_message` 表记录 message_id);
- **取消**:`delivered` 后 24 小时、`cancelled/superseded/refunded` 立即;由定时任务执行 `unpinChatMessage(message_id)`。
- **服务消息**:置顶成功后,若下一条 update 中出现该聊天的 `pinned_message` 服务消息,或立即用 `message_id+1` 试删【假设:私聊中服务消息属于机器人"outgoing",可删;需真机验证】;失败静默。
- Business:默认关闭;打开后走 `api_kwargs={"business_connection_id":…}`。

### 8.4 清理(R-UX-1)
| 对象 | 处理 | 时机 |
|---|---|---|
| 问答进度卡 | 原位演进 → 收起为一行 | 实时 |
| 已解决的已知问题卡 | 收起为"✓ 已解决" | 点击后 |
| 旧欢迎卡(同一 case 内重复出现的) | 去掉按钮,正文末尾加 zh `(已更新,请看下方)` en `(Updated — see below)` | 新卡发出时 |
| 被替代的报价卡 | 改写为 zh `[[cross]] 此报价已更新为订单 {new_id},请以最新一张为准。` en `[[cross]] This quote has been replaced by order {new_id}. Please use the latest one.`,去掉按钮 | 新报价时 |
| `order.checking` 临时消息 | 删除 | 核验完成 |
| 人机验证题 | 通过后删除 | 通过时 |
| 置顶服务消息 | 删除(尽力) | 置顶后 |
- 所有编辑/删除在 48 小时内;超时只做"去按钮"(editMessageReplyMarkup 无时限限制以外的情况失败静默)。

### 8.5 反应(R-UX-5,仅 Bot 私聊)
- 客户发付款截图 → 👌;人工锁中客户发的消息(已转发同事)→ ✍(每 30 分钟最多 1 次,与 pending_status 卡互斥:发卡时不加反应)。
- 失败静默;配置可关。

### 8.6 "思考中"(R-UX-4,仅 Bot 私聊)
- AI 回合开始(预占额度成功后)→ `sendMessageDraft(chat_id, draft_id=<case_id*1000+turn>, text="", can_stop=True)`(空文本 = 客户端显示 "Thinking…" 占位);每 20 秒刷新一次(草稿为 30 秒临时预览)直到回复就绪;回复以普通 `sendMessage` 发送(草稿自动消失)。
- 客户点"停止" → 收到 `stopped_message_generation`(PTB 21.3 中位于 `update.api_kwargs`,已本地验证)→ `epochs.bump` → 本轮作废、`settle_reply(success=False)`、不发送任何内容。
- 失败(旧客户端/接口报错)→ 退回现有 `send_chat_action(typing)`。
- Business:继续用 typing(`send_chat_action` 支持 `business_connection_id`)。

---

## 9. 信任与合规设计

### 9.1 官方白名单(配置 `trust.official_accounts`、`trust.official_addresses`)
- 账号:`[{username, role_zh, role_en}]`,默认取官网列出的账号(Sonnet 写入默认值时**只放**:`oxbaimao`(软件咨询)、`oxbaimaobot`(官方购买机器人)、`baimao_interactive_bot`(本机器人);其余由业主在后台补充 —— 不擅自把官网上的个人号放进白名单)。
- 地址:`[{network, address, purpose}]`,与 WP8 收款配置共用;**修改属高危操作**(04 §5)。

### 9.2 合规底线(代码锁定,管理员只能加严)
- AI 不承诺效果(回复率、过验证率、收益)、不承诺认证通过、不介绍规避平台风控/身份冒用的方法。
- 敏感业务线不进入 AI。
- 知识库中 `scraped_services`/`scraped_products`(空投收益宣传)标记为 `quarantined`,不注入、不授权金额(现状已不注入,WP1 加显式隔离与测试)。
- 业务线 notes 中出现"保证/包过/无限/稳赚/100%"等词 → 配置发布时 lint 警告(不阻止,但需二次确认)。

### 9.3 唯一金额(WP8)
- 订单金额 = 基础价 + 0.01–0.99 的唯一尾数(在同一收款地址、未结订单范围内唯一),例如 288 → 288.07。
- 目的:无 TxID 时也能按"金额 + 地址 + 时间窗"自动匹配;客户可读性仍好。
- 尾数让客户多付 ≤0.99U:订单卡写明"含识别尾数 0.07";业主可在配置中改为"减尾数"(288 → 287.93)以示诚意。默认:**减尾数**(客户永远不会因识别码多付)。

---

## 10. 成交与售后流程总图

```
先答卡 ─▶ 问答 ─▶ AI 剧本 ─▶ 出口
                        ├─ software:CTA 官方入口(本机器人不出单)
                        └─ 其他:handoff quote_request ─▶ 同事"出报价"向导 ─▶ 订单卡(置顶)
                                                                    │
                       Business 客户:同时发 biz.bind_bot,订单卡发在 Bot 私聊 ◀─┘
订单卡 ─▶ 客户付款 ─▶ (TxID 提交 | 自动按金额匹配) ─▶ 核验
           ├─ exact / 容差内 ─▶ paid(🎉,订单卡更新)─▶ 同事"开始交付" ─▶ fulfilling ─▶ "标记已交付" ─▶ delivered(🎉)─▶ 24h 回访
           ├─ 少付 ─▶ payment_review(P0)+ 补差卡
           ├─ 多付/重复 ─▶ payment_review(P0)+ 说明卡
           └─ 查不到 ─▶ P0 + 网络确认卡
售后:先答(已知问题卡)─▶ 问答 ─▶ 自助解决 | AI 排查 | 升级矩阵
```
