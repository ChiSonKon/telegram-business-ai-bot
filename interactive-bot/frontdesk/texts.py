"""前台中英双语文案。所有面向客户的固定文本集中于此,按 lang 取值。"""
from __future__ import annotations

T = {
    "zh": {
        "welcome": (
            "[[robot]] <b>欢迎来到白猫工作室</b>\n\n"
            "很高兴为您服务。请选择您本次的业务来意，先帮您把需求快速梳理清楚，再为您对接合适的顾问：\n\n"
            "[[user]] <b>您这次是来：</b>"
        ),
        "welcome_back": "[[robot]] <b>欢迎来到白猫工作室</b>\n\n欢迎回来！这次是继续上次的问题，还是有新的业务需求？点下方按钮或直接告诉我：",
        "btn_prospect": "🛡️ 了解产品 / 准备购买",
        "btn_support": "🛠️ 已购买 · 售后支持",
        "btn_partner": "💼 商务合作 / 渠道代理",
        "btn_visitor": "🌐 浏览全部业务矩阵",
        "pending_short": "您的问题已在处理中，同事会在这里回复您。",
        "intent_human": "[[chat]] <b>留言已开启</b>\n请直接留下需求或问题，我们的同事看到后会在这里回复您，无需重复提交。",
        "intent_default": "[[info]] 请告诉我您这次想了解什么？",
        "ended": "[[chat]] 您的消息已转给同事,他们会在这里回复您,不需要重复发送。",
        "quota_ended": "[[clock]] 这次我能帮的部分差不多了,接下来交给同事继续。我们的对话已保存,同事会在这里回复您。",
        "offline": "[[alert]] 我这边暂时没能生成回复。您的问题已记下并转给同事,不需要重复发送。",
        "needs_human": "[[chat]] 这一点涉及具体价格或付款,为避免说错,我请同事来确认。问题已保存,不需要重复发送。",
        "offer_leave": "[[chat]] 想继续深入了解，或需要同事进一步沟通？可以随时留言给我们。",
        "btn_leave": "留言给同事",
        "hold": "[[info]] 我们先把需求整理清楚，整理完之后随时可以留言给同事。",
        "pick_first": "请先选择上面的来意；需求整理完、和 AI 顾问聊过之后，可以随时留言给同事。",
    },
    "en": {
        "welcome": (
            "[[robot]] <b>Welcome to WhiteCat Studio</b>\n\n"
            "Glad to assist you. Please choose the reason for your visit — we'll quickly organize your requirements and connect you with a dedicated advisor:\n\n"
            "[[user]] <b>What can we help with?</b>"
        ),
        "welcome_back": "[[robot]] <b>Welcome to WhiteCat Studio</b>\n\nWelcome back! Are we continuing last time, or exploring something new? Tap below or tell me:",
        "btn_prospect": "🛡️ Explore Products / Buy",
        "btn_support": "🛠️ Customer Support",
        "btn_partner": "💼 Partnership / Reseller",
        "btn_visitor": "🌐 Browse All Services",
        "pending_short": "We're already on it — a teammate will reply here.",
        "intent_human": "[[chat]] <b>Message for our team</b>\nLeave your request here. A teammate will reply in this chat — no need to resend.",
        "intent_default": "[[info]] What would you like to know?",
        "ended": "[[chat]] Your message is with a teammate now — they'll reply here, no need to resend.",
        "quota_ended": "[[clock]] I've covered what I can for now — a teammate will take it from here. Our chat is saved and they'll reply right here.",
        "offline": "[[alert]] I couldn't put a reply together just now. Your question is saved and passed to a teammate — no need to resend.",
        "needs_human": "[[chat]] This involves exact pricing or payment, so I've asked a teammate to confirm rather than guess. Your question is saved — no need to resend.",
        "offer_leave": "[[chat]] Want to go deeper, or talk it through with a teammate? You can leave us a message any time.",
        "btn_leave": "Message a teammate",
        "hold": "[[info]] Let's get your request sorted first — once that's done you can leave a message for a teammate any time.",
        "pick_first": "Please pick one of the options above first; after your request is captured and you've chatted with the advisor, you can message a teammate any time.",
    },
}


def t(key: str, lang: str = "zh") -> str:
    return T.get(lang, T["zh"]).get(key) or T["zh"][key]
