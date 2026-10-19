"""客户可见文案默认值(03 号设计文档定稿原文,中英)。

占位符:{x} 为 str.format 字段;[[icon]] 为 ui.icons 图标占位。
动态列表(价目行、官方账号列表等)由代码渲染后以 {rows}/{list} 等字段注入。
"""
from __future__ import annotations

COPY: dict[str, dict[str, str]] = {
    # ---------- 入口 ----------
    "welcome": {
        "zh": (
            "[[robot]] <b>欢迎来到白猫工作室</b>\n\n"
            "请先点下面的按钮,选一下您需要的业务。\n"
            "价格、功能、售后问题,我会先帮您找到答案。\n\n"
            "<i>官网 web3baimao.com · 白猫不会向您索要密码、验证码或助记词</i>"
        ),
        "en": (
            "[[robot]] <b>Welcome to WhiteCat Studio</b>\n\n"
            "Please tap a button below to pick what you need first.\n"
            "Pricing, features or an issue with an order — I'll get you an answer first.\n\n"
            "<i>Website web3baimao.com · We'll never ask for your password, login code or seed phrase</i>"
        ),
    },
    "welcome_back_recent": {
        "zh": "[[robot]] <b>欢迎回来</b>\n上次聊到:{summary}\n要接着上次的继续吗?",
        "en": "[[robot]] <b>Welcome back</b>\nLast time we talked about: {summary}\nShall we pick up where we left off?",
    },
    "welcome_back_old_prefix": {
        "zh": "上次您咨询过:{summary}\n\n",
        "en": "Last time you asked about: {summary}\n\n",
    },
    "btn.continue": {"zh": "继续上次的", "en": "Continue"},
    "btn.new_topic": {"zh": "这次是新问题", "en": "Something new"},
    "pending_status": {
        "zh": "[[clock]] <b>您的问题已在处理中</b>\n{reason_line}已等待 {wait}。同事会在这里回复您,不需要重复发送。\n有新情况可以直接补充。",
        "en": "[[clock]] <b>We're on it</b>\n{reason_line}Waiting time so far: {wait}. A teammate will reply right here — no need to resend.\nFeel free to add any new details.",
    },
    "btn.different_question": {"zh": "这是另一个新问题", "en": "This is a different question"},
    "pending_short": {
        "zh": "您的问题已在处理中,同事会在这里回复您。",
        "en": "We're already on it — a teammate will reply here.",
    },
    "btn.software": {"zh": "🛒 买软件 / 看价格", "en": "🛒 Software & pricing"},
    "btn.custom": {"zh": "🤖 定制机器人", "en": "🤖 Custom bots"},
    "btn.more": {"zh": "🌐 静态 IP · AI 账号 · 其他", "en": "🌐 Static IPs · AI accounts · More"},
    "btn.support": {"zh": "🛠 已购买 · 售后", "en": "🛠 I'm a customer — support"},
    "btn.partner": {"zh": "🤝 渠道合作", "en": "🤝 Partnerships"},
    "btn.more.network": {"zh": "🌐 静态 IP", "en": "🌐 Static IPs"},
    "btn.more.ai": {"zh": "🧠 AI 账号", "en": "🧠 AI accounts"},
    "btn.more.other": {"zh": "💬 其他业务", "en": "💬 Something else"},
    "software_ai_ack": {
        "zh": "好的,关于 WhiteCat 5.0,我先回答您 👇",
        "en": "Sure — here's what you need to know about WhiteCat 5.0 👇",
    },

    # ---------- 先答卡 ----------
    "card.price_software": {
        "zh": (
            "[[package]] <b>WhiteCat 5.0 · 授权价格</b>\n新设备自带免费试用,可以先用再买。\n\n"
            "{rows}\n\n以上为单设备价格,以软件内购买页实时显示为准。"
        ),
        "en": (
            "[[package]] <b>WhiteCat 5.0 · License pricing</b>\nNew devices come with a free trial, so you can try before you buy.\n\n"
            "{rows}\n\nPrices are per device; the in-app checkout always shows the live price."
        ),
    },
    "card.price_overview": {
        "zh": (
            "[[package]] <b>白猫工作室 · 服务与参考价</b>\n\n"
            "• WhiteCat 5.0 获客软件 —— 6.9 U 起,新设备可免费试用\n"
            "• Telegram 机器人定制 —— 288 U 起,按功能报价\n"
            "• 独享静态 IP —— 0.43 U/个起,量大价优\n"
            "• AI 大模型账号 —— ChatGPT Plus 参考价 23 U\n\n"
            "以上为公开参考价,成交以购买页或报价单为准。想了解哪一项?"
        ),
        "en": (
            "[[package]] <b>WhiteCat Studio · Services & reference prices</b>\n\n"
            "• WhiteCat 5.0 growth software — from 6.9 U, free trial on new devices\n"
            "• Custom Telegram bots — from 288 U, quoted by scope\n"
            "• Dedicated static IPs — from 0.43 U each, better rates in bulk\n"
            "• AI model accounts — ChatGPT Plus from 23 U (reference)\n\n"
            "These are public reference prices; the final price is on the checkout page or your quote. Which one interests you?"
        ),
    },
    "btn.official_buy": {"zh": "🛒 官方购买入口", "en": "🛒 Official checkout"},
    "btn.help_choose": {"zh": "🤔 帮我选套餐", "en": "🤔 Help me choose"},
    "btn.other_services": {"zh": "🧩 了解其他服务", "en": "🧩 Other services"},
    "btn.ov.software": {"zh": "软件", "en": "Software"},
    "btn.ov.custom": {"zh": "定制", "en": "Custom bot"},
    "btn.ov.network": {"zh": "静态 IP", "en": "Static IP"},
    "btn.ov.ai": {"zh": "AI 账号", "en": "AI accounts"},
    "btn.ov.other": {"zh": "其他", "en": "Other"},
    "card.official_buy": {
        "zh": (
            "[[check]] <b>官方购买入口</b>\n两种方式都会自动发放卡密并激活:\n\n"
            "1. 在软件内打开「购买」页,一键下单(推荐)\n2. 官方购买机器人 @oxbaimaobot\n\n"
            "买完遇到任何问题,回到这里发订单号或卡密前 4 位即可。"
        ),
        "en": (
            "[[check]] <b>Official checkout</b>\nBoth options deliver and activate your license key automatically:\n\n"
            "1. Open the Purchase page inside the app (recommended)\n2. Our official purchase bot @oxbaimaobot\n\n"
            "If anything goes wrong after buying, come back here with your order number or the first 4 characters of your key."
        ),
    },
    "btn.open_buy_bot": {"zh": "打开 @oxbaimaobot", "en": "Open @oxbaimaobot"},
    "btn.download": {"zh": "下载软件", "en": "Download the app"},
    "btn.have_question": {"zh": "还有问题想问", "en": "I have a question"},
    "q.software.horizon": {
        "zh": "[[question]] <b>打算用多久?</b>\n不确定也没关系,选个大概。",
        "en": "[[question]] <b>How long do you expect to use it?</b>\nA rough guess is fine.",
    },
    "opt.software.horizon.trial": {"zh": "先试几天", "en": "Just trying it"},
    "opt.software.horizon.1m": {"zh": "1 个月", "en": "About a month"},
    "opt.software.horizon.3m": {"zh": "3 个月", "en": "About 3 months"},
    "opt.software.horizon.1y": {"zh": "1 年以上", "en": "A year or more"},
    "opt.software.horizon.multi": {"zh": "长期 + 多台设备", "en": "Long-term, several devices"},
    "card.software_recommend": {
        "zh": "[[sparkles]] <b>建议:{plan}({price} U · {duration})</b>\n{reason}\n\n{compare}",
        "en": "[[sparkles]] <b>Our pick: {plan} ({price} U · {duration})</b>\n{reason}\n\n{compare}",
    },
    "btn.ask_details": {"zh": "💬 问问细节", "en": "💬 Ask a question"},
    "card.is_bot": {
        "zh": "我是白猫工作室的 AI 助理。简单的问题我可以马上回答;需要同事处理的,说一句您的事,我会直接转给他们。这里的对话同事都能看到。",
        "en": "I'm WhiteCat Studio's AI assistant. I can answer the quick questions right away; for anything that needs a teammate, just tell me what it's about and I'll pass it on. Our team can see this chat.",
    },
    "card.custom_intro": {
        "zh": (
            "[[robot]] <b>Telegram 机器人定制</b>\n按功能复杂度报价,<b>288 U 起</b>;谈好的价格不会随意上涨。\n"
            "常见类型:AI 双向客服、发卡商城、监听引流、链上交易机器人、业务自动化脚本。\n"
            "流程:说需求 → 出报价单 → 开工交付 → 验收售后。\n\n为了给您一个靠谱的方向,我问 2–3 个小问题,都可以跳过。"
        ),
        "en": (
            "[[robot]] <b>Custom Telegram bots</b>\nPriced by scope, <b>from 288 U</b> — and once we agree a price, it stays put.\n"
            "Popular builds: AI customer-service bots, storefront bots, lead-capture listeners, on-chain trading bots and workflow automation.\n"
            "How it works: tell us what you need → get a written quote → we build → you review, with after-sales support.\n\n"
            "A few quick questions (all optional) so we can point you in the right direction."
        ),
    },
    "card.custom_low_budget": {
        "zh": "[[info]] 定制起步价是 288 U,300 U 以内通常能做单一功能的轻量机器人。如果主要是自动回复咨询,也可以先看看我们的现成方案,我接下来帮您对比一下。",
        "en": "[[info]] Custom builds start at 288 U, and under 300 U usually covers a lean, single-purpose bot. If it's mainly answering enquiries, a ready-made option may fit too — I'll compare them for you next.",
    },
    "card.network_intro": {
        "zh": (
            "[[globe]] <b>独享静态 IP</b>\n独享住宅 / 机房静态 IP,到期前 30 天提醒续费。\n\n"
            "<b>机房静态 IP</b>:\n"
            "• 1~10 条 —— 2 U/条起\n"
            "• 10~20 条 —— 1.5 U/条\n"
            "• 20~200 条 —— 1 U/条\n"
            "• 200~400 条 —— 0.5 U/条\n"
            "• 500 条以上 —— 0.43 U/条\n\n"
            "<b>住宅静态 IP</b>:\n"
            "• 1~20 条 —— 3 U/条\n"
            "• 20 条以上 —— 2 U/条\n\n"
            "库存与实时价格由同事确认。"
        ),
        "en": (
            "[[globe]] <b>Dedicated static IPs</b>\nResidential & datacenter static IPs, renewal reminder 30 days before expiry.\n\n"
            "<b>Datacenter static IPs</b>:\n"
            "• 1–10 IPs — from 2 U/ea\n"
            "• 10–20 IPs — 1.5 U/ea\n"
            "• 20–200 IPs — 1 U/ea\n"
            "• 200–400 IPs — 0.5 U/ea\n"
            "• 500+ IPs — 0.43 U/ea\n\n"
            "<b>Residential static IPs</b>:\n"
            "• 1–20 IPs — 3 U/ea\n"
            "• 20+ IPs — 2 U/ea\n\n"
            "A teammate confirms live stock and pricing."
        ),
    },
    "card.ai_intro": {
        "zh": (
            "[[sparkles]] <b>AI 大模型账号</b>\n"
            "• ChatGPT Plus —— 参考价 23 U(手搓代充 / 成品)\n"
            "• Gemini Pro 成品 —— 参考价 8 U\n"
            "• ChatGPT Pro —— 5x 120 U | 20x 210 U | 25x 510 U\n"
            "• Claude —— 暂无现货报价,请联系同事确认\n\n"
            "其他产品与库存由同事确认。白猫不会向您索要账号密码或验证码。"
        ),
        "en": (
            "[[sparkles]] <b>AI model accounts</b>\n"
            "• ChatGPT Plus — from 23 U (manual top-up / pre-made)\n"
            "• Gemini Pro pre-made — from 8 U\n"
            "• ChatGPT Pro — 5x 120 U | 20x 210 U | 25x 510 U\n"
            "• Claude — currently unquoted, contact a teammate to confirm\n\n"
            "A teammate confirms other products and stock. We'll never ask for your account password or login code."
        ),
    },

    # ---------- 真人 ----------
    "human.ask": {
        "zh": "[[chat]] 好的。用一句话说说您要办的事,我连同记录一起交给同事。",
        "en": "[[chat]] Sure. Tell me in one sentence what you need, and I'll pass it to a teammate along with our chat.",
    },
    "human.done": {
        "zh": "[[check]] <b>已交给同事</b>\n{eta}\n这里的记录对方都能看到,不需要重复发送。",
        "en": "[[check]] <b>Passed to a teammate</b>\n{eta}\nThey can see everything here, so there's no need to repeat yourself.",
    },
    "human.cancelled": {"zh": "好的,有需要随时说。", "en": "No problem — just message me anytime."},
    "eta.day": {"zh": "通常 {n} 分钟内在这里回复您。", "en": "They usually reply here within {n} minutes."},
    "eta.night": {"zh": "现在是夜间,同事会在上午 {h} 点前回复您。", "en": "It's night here; they'll reply by {h} AM (UTC+8)."},
    "eta.night_p0": {"zh": "已加急,最晚上午 {h} 点前回复您。", "en": "Flagged as urgent — they'll reply by {h} AM (UTC+8) at the latest."},

    # ---------- Business ----------
    "biz.disclosure": {
        "zh": "(白猫工作室 AI 助理代为接待,老板也会看到这段对话)",
        "en": "(This is WhiteCat Studio's AI assistant, replying on the owner's behalf — they can see this chat too.)",
    },
    "biz.greeting_known": {
        "zh": "在的~有什么需要直接说,我先帮您看。",
        "en": "Hi! Tell me what you need and I'll take a look first.",
    },
    "biz.greeting_new": {
        "zh": "您好,这里是白猫工作室。想了解哪方面?软件、机器人定制、静态 IP 或其他,直接说就好。",
        "en": "Hi, this is WhiteCat Studio. What can we help with — software, custom bots, static IPs or something else? Just tell me.",
    },
    "biz.bind_bot": {
        "zh": "[[link]] 订单和到账通知会发在白猫官方机器人里,方便您随时查看。点下面按钮打开即可(只需一次)。",
        "en": "[[link]] Your order and payment updates will be sent through our official bot so you can check them anytime. Tap below to open it (one-time).",
    },
    "btn.open_order": {"zh": "打开订单", "en": "Open my order"},

    # ---------- 问答 ----------
    "intake.progress_confirm": {
        "zh": "[[note]] <b>我理解的对吗?</b>\n{lines}",
        "en": "[[note]] <b>Did I get this right?</b>\n{lines}",
    },
    "intake.collapsed": {"zh": "✓ 需求已记下:{line_name} · {need_short}", "en": "✓ Noted: {line_name} · {need_short}"},
    "intake.resume_prefix": {"zh": "接着刚才的:", "en": "Picking up where we left off: "},
    "intake.side_noted": {
        "zh": "这个问题我记下了,问完这一两个小问题后马上回答您。",
        "en": "Noted — I'll answer that right after one or two quick questions.",
    },
    "intake.image_received": {
        "zh": "收到图片。我先把问题问完,马上就好。",
        "en": "Got the image. Just a couple of quick questions first.",
    },
    "intake.skip": {"zh": "跳过", "en": "Skip"},
    "intake.btn_ok": {"zh": "✅ 对,继续", "en": "✅ Yes, continue"},
    "intake.btn_fix": {"zh": "✏️ 改一下", "en": "✏️ Let me fix something"},

    "software_ai_ack_multi": {
        "zh": "收到,您的 {n} 个问题我一条条回答 👇",
        "en": "Got your {n} questions — I'll take them one by one 👇",
    },
    "escalation.ask_payment": {
        "zh": "[[info]] 请用一句话告诉我付款的时间、金额和交易哈希(如果有),我连同记录交给财务同事核对。",
        "en": "[[info]] Please tell me in one line when you paid, how much, and the transaction hash if you have it — I'll pass it to our finance team with this chat.",
    },
    "escalation.ask_need": {
        "zh": "[[chat]] 能用一句话说说您想做什么吗?我连同记录交给负责的同事。",
        "en": "[[chat]] Could you tell me in one sentence what you'd like to build or buy? I'll pass it to the right teammate along with our chat.",
    },
    "intake.marker_buttons": {
        "zh": "(客户通过按钮完成了选择,没有额外文字)",
        "en": "(The customer completed the choices with buttons and typed nothing.)",
    },
    "resume.continue_ack": {
        "zh": "好的,我们接着上次的聊。您直接说。",
        "en": "Sure — let's pick up where we left off. Go ahead.",
    },
    "intake.fix_which": {"zh": "[[edit]] <b>改哪一项?</b>", "en": "[[edit]] <b>Which one would you like to change?</b>"},

    # ---------- 核验 ----------
    "verify.account_ok": {
        "zh": "[[check]] <b>@{u} 是白猫官方账号</b>({role})。",
        "en": "[[check]] <b>@{u} is an official WhiteCat account</b> ({role}).",
    },
    "verify.account_bad": {
        "zh": "[[alert]] <b>@{u} 不是白猫官方账号,请不要向它付款。</b>",
        "en": "[[alert]] <b>@{u} is not an official WhiteCat account. Please don't send it any money.</b>",
    },
    "verify.addr_ok": {
        "zh": "[[check]] 这个地址是白猫的收款地址({network},尾号 {tail})。付款前请确认它出现在本机器人发给您的、带订单号的订单卡里。",
        "en": "[[check]] This is a WhiteCat payment address ({network}, ending {tail}). Before paying, make sure it appears on an order card with an order number sent by this bot.",
    },
    "verify.addr_bad": {
        "zh": "[[alert]] <b>这不是白猫的收款地址。</b>白猫只在本机器人发出的、带订单号的订单卡里提供收款地址。",
        "en": "[[alert]] <b>This is not a WhiteCat payment address.</b> We only share payment addresses on order cards with an order number, sent by this bot.",
    },
    "verify.list": {
        "zh": (
            "白猫官方账号:\n{list}\n\n"
            "官方站点:\n"
            "• 综合业务、成交案例与客户好评主站:web3baimao.com\n"
            "• 获客软件(WhiteCat 5.0)官网:tg.web3baimao.com\n"
            "• 空投业务官网:airdrop.web3baimao.com"
        ),
        "en": (
            "Official WhiteCat accounts:\n{list}\n\n"
            "Official Websites:\n"
            "• Main site (all services, deals & customer reviews): web3baimao.com\n"
            "• Software site (WhiteCat 5.0): tg.web3baimao.com\n"
            "• Airdrop business site: airdrop.web3baimao.com"
        ),
    },
    "verify.scammed": {
        "zh": "如果已经转账,请保留转账记录,并在对方资料页点「举报」。第三方收走的款项我们无法追回,但会把这个账号记录下来提醒其他客户。",
        "en": "If you've already paid, keep the transaction record and report the account from its profile. We can't recover funds sent to third parties, but we'll log this account to warn others.",
    },
    "order.not_found_query": {
        "zh": "[[alert]] 没有找到订单 {id}。白猫的订单号由本机器人生成,如果有人发给您一个查不到的订单号,请提高警惕。",
        "en": "[[alert]] We couldn't find order {id}. Our order numbers are generated by this bot — if someone gave you one that doesn't exist here, please be careful.",
    },
    "order.view": {
        "zh": "[[package]] <b>订单 {id}</b>\n{title}\n状态:{state}\n金额:{amount} {currency}",
        "en": "[[package]] <b>Order {id}</b>\n{title}\nStatus: {state}\nAmount: {amount} {currency}",
    },
    "order.exists_only": {
        "zh": "订单 {id} 存在。出于隐私,详情只对订单所属客户显示。",
        "en": "Order {id} exists. For privacy, details are shown only to the customer who owns it.",
    },
    "order.none": {"zh": "您还没有订单。", "en": "You don't have any orders yet."},

    # ---------- 敏感 ----------
    "card.sensitive": {
        "zh": (
            "[[note]] <b>{line_name}</b>\n"
            "支持承接各类 KYC 认证业务，按软件平台与国家地区评估定价：\n"
            "• 各类交易所 KYC —— 20 U 起\n"
            "• 东南亚国家地区 —— 20 U 起\n"
            "• 欧美国家地区 —— 80 U 起\n"
            "• Sumsub 平台认证 —— 80 U\n"
            "• 其他软件与定制需求 —— 由同事评估报价\n\n"
            "请说明您的目标软件/平台、国家地区与具体需求，同事会在这里回复您。\n"
            "白猫不会索要您的账户密码、验证码或助记词。"
        ),
        "en": (
            "[[note]] <b>{line_name}</b>\n"
            "We undertake KYC verification services, assessed by platform & country:\n"
            "• Crypto exchanges — from 20 U\n"
            "• Southeast Asia countries — from 20 U\n"
            "• Europe / US countries — from 80 U\n"
            "• Sumsub platform — 80 U\n"
            "• Other apps / custom needs — quoted by a teammate\n\n"
            "Please specify your platform, target country and requirements, and a teammate will reply here.\n"
            "We will never ask for your account password, login code or seed phrase."
        ),
    },
    "card.sensitive_done": {
        "zh": "[[check]] 已登记,同事评估后会在这里回复您。",
        "en": "[[check]] Got it. A teammate will review and reply here.",
    },

    # ---------- 售后 ----------
    "support.known.install_win": {
        "zh": (
            "[[info]] <b>Windows 提示\"已保护你的电脑\"?</b>\n软件暂未购买代码签名,首次运行可能被拦截,这是正常的:\n"
            "1. 点击「更多信息」\n2. 点击「仍要运行」\n完整图文步骤见安装指南。"
        ),
        "en": (
            "[[info]] <b>Windows says \"Windows protected your PC\"?</b>\nThe app isn't code-signed yet, so the first launch may be blocked. That's expected:\n"
            "1. Click \"More info\"\n2. Click \"Run anyway\"\nSee the install guide for the full walkthrough."
        ),
    },
    "support.known.install_mac": {
        "zh": "macOS 提示\"无法打开,因为无法验证开发者\"?打开「系统设置 → 隐私与安全性」,在下方找到被拦截的 WhiteCat,点「仍要打开」。",
        "en": "macOS says the app \"can't be opened because the developer cannot be verified\"? Open System Settings → Privacy & Security, find WhiteCat near the bottom and click \"Open Anyway\".",
    },
    "btn.install_guide": {"zh": "安装指南", "en": "Install guide"},
    "btn.resolved": {"zh": "✅ 解决了", "en": "✅ That fixed it"},
    "btn.not_resolved": {"zh": "❌ 还是不行", "en": "❌ Still not working"},
    "support.resolved": {"zh": "✓ 已解决", "en": "✓ Resolved"},
    "support.license.ask": {
        "zh": "[[lock]] 请发一下卡密的<b>前 4 位</b>和大概的购买时间,我交给负责授权的同事核对。\n请不要把完整卡密发给任何人,包括自称白猫客服的人。",
        "en": "[[lock]] Please send the <b>first 4 characters</b> of your key and roughly when you bought it. I'll pass it to the teammate who handles licenses.\nDon't share your full key with anyone — including people claiming to be WhiteCat support.",
    },
    "support.license.full_key_warning": {
        "zh": "为了安全,我只需要前 4 位。完整卡密请不要发给任何人,已为您撤回这条消息。",
        "en": "For your security I only need the first 4 characters. Please don't share your full key with anyone — I've removed that message for you.",
    },
    "support.refund": {
        "zh": "[[note]] 退款需要同事核对订单和使用情况。已为您登记,同事会在这里联系您。{eta}",
        "en": "[[note]] Refunds need a teammate to check the order and usage. I've logged your request and they'll contact you here. {eta}",
    },
    "support.renew_software": {
        "zh": "24 小时内从散户价升级会员或合伙人,差价全额退还(以订单核对为准)。",
        "en": "Upgrade within 24 hours and we'll refund the difference in full (subject to an order check).",
    },

    # ---------- 情绪 ----------
    "care.angry_paid": {
        "zh": "非常理解您的着急。您的付款已确认到账(订单 {id},{amount} USDT,{paid_at} 到账),已加急通知负责同事。{eta}",
        "en": "I completely understand. Your payment is confirmed (order {id}, {amount} USDT, received {paid_at}), and I've flagged it as urgent for the team. {eta}",
    },
    "care.angry": {
        "zh": "抱歉让您不愉快。您可以直接说想解决的问题,我尽量马上帮您处理;需要同事的话也可以直接说。",
        "en": "Sorry this has been frustrating. Tell me what you need sorted and I'll do my best right away — or just say if you'd like a teammate.",
    },

    # ---------- 降级 ----------
    "offline": {
        "zh": "[[alert]] 我这边暂时没能生成回复。您的问题已记下并转给同事,不需要重复发送。",
        "en": "[[alert]] I couldn't put a reply together just now. Your question is saved and passed to a teammate — no need to resend.",
    },
    "quota_ended": {
        "zh": "[[clock]] 这次我能帮的部分差不多了,接下来交给同事继续。我们的对话已保存,同事会在这里回复您。",
        "en": "[[clock]] I've covered what I can for now — a teammate will take it from here. Our chat is saved and they'll reply right here.",
    },
    "needs_human": {
        "zh": "[[chat]] 这一点涉及具体价格或付款,为避免说错,我请同事来确认。问题已保存,不需要重复发送。",
        "en": "[[chat]] This involves exact pricing or payment, so I've asked a teammate to confirm rather than guess. Your question is saved — no need to resend.",
    },
    "voice.reply": {
        "zh": "抱歉,我暂时听不了语音 🙏 方便打几个字吗?",
        "en": "Sorry, I can't listen to voice messages yet 🙏 Could you type it out?",
    },
    "ai_voice_label": {"zh": "(AI 语音)", "en": "(AI voice)"},

    # ---------- 订单与支付(WP8 使用) ----------
    "order.quote": {
        "zh": (
            "[[payment]] <b>报价单 · {id}</b>\n{title}\n{detail}\n\n金额:<b>{amount} USDT</b>\n"
            "网络:<b>{network}</b>(只支持此网络,转错网络无法到账)\n收款地址:<code>{address}</code>\n地址核对:{head}…{tail}\n有效期至:{expires_local}\n\n"
            "请确保<b>到账金额</b>为 {amount} USDT(交易所手续费请另付)。\n付款后会自动确认,一般 1–5 分钟;也可以点下方按钮提交交易哈希加快核对。"
        ),
        "en": (
            "[[payment]] <b>Quote · {id}</b>\n{title}\n{detail}\n\nAmount: <b>{amount} USDT</b>\n"
            "Network: <b>{network}</b> (this network only — transfers on other networks won't arrive)\nAddress: <code>{address}</code>\nCheck: {head}…{tail}\nValid until: {expires_local}\n\n"
            "Please make sure we <b>receive</b> exactly {amount} USDT (exchange fees are on the sender).\nPayments are confirmed automatically, usually within 1–5 minutes. You can also submit the transaction hash below to speed things up."
        ),
    },
    "btn.pay_txid": {"zh": "我已付款,提交交易哈希", "en": "I've paid — submit tx hash"},
    "btn.pay_help": {"zh": "有疑问,联系同事", "en": "Question? Contact a teammate"},
    "order.ask_txid": {
        "zh": "好的,请把交易哈希(TxID)粘贴发给我。在交易所的提币记录里可以复制到。",
        "en": "Great — please paste the transaction hash (TxID). You'll find it in your exchange's withdrawal history.",
    },
    "order.receipt": {
        "zh": "收到截图。为了自动核对到账,请把<b>交易哈希(TxID)</b>发给我;没有也没关系,系统也会按金额自动查找,通常 1–5 分钟。",
        "en": "Got your screenshot. To match it automatically, please send the <b>transaction hash (TxID)</b>. If you don't have it, that's fine — we also look it up by amount, usually within 1–5 minutes.",
    },
    "order.checking": {"zh": "[[clock]] 正在核对这笔交易…", "en": "[[clock]] Checking this transaction…"},
    "order.paid": {
        "zh": "[[check]] <b>已收到您的付款</b>\n订单 {id} · {amount} USDT · {paid_at}\n{next_step}\n可以随时发送 /order {id} 查看进度。",
        "en": "[[check]] <b>Payment received</b>\nOrder {id} · {amount} USDT · {paid_at}\n{next_step}\nSend /order {id} anytime to check progress.",
    },
    "order.paid_tolerance_suffix": {
        "zh": "(实收 {received} USDT,手续费差额已为您忽略)",
        "en": "(Received {received} USDT — we've waived the fee difference.)",
    },
    "order.underpaid": {
        "zh": "[[info]] 已收到 {received} USDT,距订单金额还差 <b>{diff} USDT</b>。\n您可以把差额转到同一地址(同一网络),或联系同事处理。订单已为您保留。",
        "en": "[[info]] We've received {received} USDT — <b>{diff} USDT</b> short of the order total.\nYou can send the difference to the same address (same network), or ask a teammate to help. Your order is on hold for you.",
    },
    "btn.topped_up": {"zh": "我补上了,提交交易哈希", "en": "I've topped up — submit tx hash"},
    "btn.contact_teammate": {"zh": "联系同事", "en": "Contact a teammate"},
    "order.overpaid": {
        "zh": "[[info]] 我们收到了 {received} USDT,比订单金额多 {diff} USDT(或是重复付款)。同事会联系您处理退回或抵扣,不需要您做什么。",
        "en": "[[info]] We received {received} USDT, {diff} USDT more than the order total (or a duplicate payment). A teammate will contact you about a refund or credit — nothing you need to do.",
    },
    "order.not_found": {
        "zh": "[[alert]] 在 {network} 网络上没有找到这笔交易。请确认提币时选择的网络是 {network};如果选了其他网络,请把交易哈希和网络名称发给我,同事会帮您核实。",
        "en": "[[alert]] We couldn't find this transaction on {network}. Please check that you chose {network} when withdrawing. If you used another network, send me the tx hash and the network name and a teammate will look into it.",
    },
    "order.delivered": {
        "zh": "[[party]] <b>订单 {id} 已交付</b>\n感谢信任白猫工作室。使用中有任何问题,直接在这里说。",
        "en": "[[party]] <b>Order {id} delivered</b>\nThanks for choosing WhiteCat Studio. If anything comes up, just message us here.",
    },
    "order.checkin": {
        "zh": "[[smile]] 订单 {id} 用得还顺利吗?",
        "en": "[[smile]] Is everything working well with order {id}?",
    },
    "btn.all_good": {"zh": "一切正常", "en": "All good"},
    "btn.need_help": {"zh": "有问题", "en": "I need help"},
    "order.renew": {
        "zh": "[[clock]] {title} 将在 {date} 到期。需要续费的话,点下方按钮,我请同事为您出续费单。",
        "en": "[[clock]] {title} expires on {date}. Tap below if you'd like to renew and we'll prepare a renewal quote.",
    },
    "btn.renew": {"zh": "续费", "en": "Renew"},
    "btn.not_now": {"zh": "暂不需要", "en": "Not now"},
    "order.superseded": {
        "zh": "[[cross]] 此报价已更新为订单 {new_id},请以最新一张为准。",
        "en": "[[cross]] This quote has been replaced by order {new_id}. Please use the latest one.",
    },
    "order.expired_late": {
        "zh": "已收到您的付款,但报价已过期,同事会尽快为您确认。",
        "en": "We've received your payment, but the quote had expired. A teammate will confirm shortly.",
    },
    "order.lookup_down": {
        "zh": "已收到您的交易哈希,系统正在核对。若 10 分钟内没有自动确认,财务同事会人工核对,您不需要重复发送。",
        "en": "Got your transaction hash — we're checking it now. If it isn't confirmed automatically within 10 minutes, our finance team will verify it manually. No need to resend.",
    },
    "order.txid_taken": {
        "zh": "这笔交易已被其他订单确认,如有疑问请联系同事。",
        "en": "This transaction has already been matched to another order. Please contact a teammate if this looks wrong.",
    },
    "order.txid_invalid": {
        "zh": "这看起来不是交易哈希,通常是 64 位字母数字。",
        "en": "That doesn't look like a transaction hash — it's usually 64 letters and numbers.",
    },

    # ---------- 转交 ----------
    "handoff.quote_request": {
        "zh": "[[check]] <b>已交给同事出报价</b>\n您的需求已整理好,同事会在这里给您一份报价单(金额、交期都会写清楚)。\n{eta}",
        "en": "[[check]] <b>Passed on for a quote</b>\nYour requirements are ready; a teammate will send you a quote here with price and timeline.\n{eta}",
    },
    "handoff.large_deal": {
        "zh": "[[check]] <b>已交给负责同事(优先处理)</b>\n合同、发票等事项同事会直接与您确认。\n{eta}",
        "en": "[[check]] <b>Passed to a senior teammate (priority)</b>\nThey'll confirm contracts, invoices and the rest with you directly.\n{eta}",
    },
    "handoff.partner_intake": {
        "zh": "[[check]] <b>合作意向已收到</b>\n负责渠道的同事会在这里联系您。可以继续补充介绍。\n{eta}",
        "en": "[[check]] <b>Partnership request received</b>\nOur partnerships lead will contact you here. Feel free to add more details.\n{eta}",
    },
    "handoff.payment_issue": {
        "zh": "[[check]] <b>付款问题已加急</b>\n财务同事会核对到账记录。\n{eta}",
        "en": "[[check]] <b>Payment issue flagged as urgent</b>\nOur finance team will check the payment records.\n{eta}",
    },
    "handoff.paid_but_broken": {
        "zh": "[[check]] <b>已交给负责授权的同事</b>\n我们会核对订单和卡密。\n{eta}",
        "en": "[[check]] <b>Passed to our licensing team</b>\nWe'll check your order and key.\n{eta}",
    },

    # ---------- 报价与付款(WP8,03 §4.13;收款走 GMPay 网关) ----------
    "order.choose": {
        "zh": "[[payment]] <b>报价单 · {id}</b>\n{title}\n{detail}\n\n金额:<b>{amount} USDT</b>\n请选择付款网络。只能用所选网络付款,转错网络无法到账:",
        "en": "[[payment]] <b>Quote · {id}</b>\n{title}\n{detail}\n\nAmount: <b>{amount} USDT</b>\nPlease choose a payment network. Only the chosen network works — transfers on other networks won't arrive:",
    },
    "order.btn_cashier": {"zh": "打开收银台", "en": "Open cashier"},
    "order.btn_switch": {"zh": "换一个网络", "en": "Use another network"},
    "order.bad_txid": {
        "zh": "这看起来不是交易哈希,通常是 64 位字母数字。",
        "en": "That doesn't look like a transaction hash — it's usually 64 letters and numbers.",
    },
    "order.expired": {
        "zh": "[[clock]] 这个报价已过期,请联系我们重新获取报价。",
        "en": "[[clock]] This quote has expired. Please ask us for a fresh one.",
    },
    "order.gateway_down": {
        "zh": "[[info]] 付款信息由同事提供,请稍等,已通知同事为您发送。",
        "en": "[[info]] A teammate will send you the payment details shortly — we've let them know.",
    },
    "order.status": {
        "zh": "[[package]] 订单 <code>{id}</code>:{title}\n状态:<b>{state}</b>",
        "en": "[[package]] Order <code>{id}</code>: {title}\nStatus: <b>{state}</b>",
    },
    "order.not_yours": {"zh": "这不是您的订单", "en": "This isn't your order"},
    "order.state.quoted": {"zh": "待选择付款网络", "en": "Choose a network"},
    "order.state.awaiting_payment": {"zh": "等待付款", "en": "Awaiting payment"},
    "order.state.payment_review": {"zh": "付款核对中", "en": "Payment being verified"},
    "order.state.paid": {"zh": "已收款,待交付", "en": "Paid"},
    "order.state.fulfilling": {"zh": "交付中", "en": "In progress"},
    "order.state.delivered": {"zh": "已交付", "en": "Delivered"},
    "order.state.cancelled": {"zh": "已取消", "en": "Cancelled"},
    "order.state.superseded": {"zh": "已被新报价取代", "en": "Replaced by a newer quote"},
    "order.state.refunded": {"zh": "已退款", "en": "Refunded"},
    "order.state.disputed": {"zh": "处理中", "en": "Under review"},

    # ---------- 多业务并行 ----------
    "multi.noted": {
        "zh": "[[check]] 收到,您同时提到了<b>{name}</b>,我也记下了。先把当前这项问完,稍后一起帮您安排。",
        "en": "[[check]] Got it — you also mentioned <b>{name}</b>, noted. Let's finish this one first, then I'll take care of both.",
    },

    # ---------- 售后与复购(WP9) ----------
    "aftercare.thanks": {"zh": "✓ 感谢反馈", "en": "✓ Thanks for letting us know"},
    "aftercare.thanks_help": {"zh": "✓ 已收到,我来帮您看看", "en": "✓ Got it — let me help"},
    "aftercare.renew_ack": {
        "zh": "✓ 已收到续费意向,同事会在这里为您出续费单。",
        "en": "✓ Noted — a teammate will prepare your renewal quote here.",
    },
    "aftercare.later_ack": {"zh": "✓ 好的,需要时随时告诉我", "en": "✓ No problem — just tell me whenever you need it"},
    "card.renew_software": {
        "zh": "[[check]] <b>软件续费与升级</b>\n续费、升级都在官方入口办理,到账后自动发放卡密并激活:\n\n1. 在软件内打开「购买」页,一键下单(推荐)\n2. 官方购买机器人 @oxbaimaobot\n\n24 小时内从散户价升级会员或合伙人,差价全额退还(以订单核对为准)。",
        "en": "[[check]] <b>Software renewal & upgrade</b>\nRenewals and upgrades are handled through the official channels — your key is issued and activated automatically:\n\n1. Open the Buy page inside the app (recommended)\n2. The official purchase bot @oxbaimaobot\n\nUpgrade within 24 hours and we'll refund the difference in full (subject to an order check).",
    },

    # ---------- 清理 ----------
    "ui.updated_see_below": {"zh": "(已更新,请看下方)", "en": "(Updated — see below)"},
}


def get(key: str, lang: str = "zh") -> str:
    entry = COPY[key]
    return entry.get(lang) or entry["zh"]
