"""流程、入口词表、升级矩阵、剧本等默认值(WP1 起逐步补全)。"""
from __future__ import annotations

# 入口分类器词表(03 §3.1)。全部小写子串匹配;顺序 1–5 的动作锁定为代码行为,只能增删关键词。
ENTRY_INTENTS: dict[str, list[str]] = {
    "verify_words": ["是你们", "是不是官方", "是官方", "真的假的", "冒充", "is this you", "is this official", "is it official", "are you official", "official account", "is this legit", "legit?"],
    "paid_words": ["付了", "已付款", "已经付", "转了", "已转", "没到账", "没收到", "未到账", "到账", "paid", "sent the money", "sent it", "transferred"],
    "negation_words": ["没", "未", "还没", "not", "didn't", "haven't", "hasn't", "never"],
    "refund": ["退款", "退钱", "refund", "chargeback"],
    "angry": ["骗子", "跑路", "投诉", "垃圾", "scam", "fraud", "scammer"],
    "human_request": ["人工", "真人", "客服", "老板", "主理人", "留言", "找人", "human", "real person", "human agent", "live agent", "the owner", "someone real", "leave a message", "talk to someone", "speak to someone"],
    "is_bot": ["你是机器人", "是机器人吗", "是ai吗", "是 ai 吗", "是真人吗", "真人吗", "are you a bot", "are you human", "are you ai", "are you a robot", "are you real"],
    "support": ["售后", "激活", "安装失败", "装不上", "打不开", "报错", "卡密", "无效", "不到账", "订单", "续费", "授权异常",
                "error", "crash", "activate", "activation", "license", "can't install", "not working", "my order", "didn't arrive"],
    "partner": ["合作", "代理", "渠道", "oem", "供应", "底价", "拿货", "分销", "代理价", "partner", "reseller", "cooperat", "agency", "white label", "wholesale"],
    "price_ask": ["多少钱", "价格", "报价", "怎么卖", "便宜", "优惠", "价钱", "收费", "price", "pricing", "how much", "cost", "quote"],
    "buy_words": ["买", "购买", "下单", "开通", "续费", "buy", "purchase", "order", "subscribe"],
    "question_words": ["吗", "呢", "怎么", "如何", "能不能", "可以", "是否", "会不会", "有没有", "多久", "?", "？", "how", "what", "can ", "does ", "do you", "is it", "will "],
    "browse": ["做什么的", "有哪些", "介绍", "what do you do", "services", "what do you offer", "有什么服务"],
    "greeting": ["你好", "在吗", "在不在", "您好", "hi", "hello", "hey", "哈喽", "有人吗"],
    "dissatisfied": ["没用", "不对", "听不懂", "useless", "wrong", "not helpful", "doesn't help"],
}

# 软件价目卡价格行的套餐英文名(知识库缺 name_en 时的回退建议,业主可在配置中改)
PLAN_NAME_EN = {
    "尝鲜体验卡": "Starter pass",
    "月度旗舰版": "Monthly",
    "季度进阶版": "Quarterly",
    "年度专业版": "Annual",
    "创世合伙人·终身版": "Founding Partner · Lifetime",
}

# 默认信任白名单:<BOT_USERNAME> 运行时由 trust 模块补入
OFFICIAL_ACCOUNTS = [
    {"username": "oxbaimao", "role_zh": "软件咨询", "role_en": "software enquiries"},
    {"username": "oxbaimaobot", "role_zh": "官方购买机器人", "role_en": "official purchase bot"},
    {"username": "<BOT_USERNAME>", "role_zh": "白猫前台", "role_en": "WhiteCat front desk"},
]

# 售后已知问题默认规则
DEFAULT_KNOWN_ISSUES = [
    {"id": "install_win", "line": "software", "os": "windows", "card_key": "support.known.install_win",
     "triggers": ["保护你的电脑", "已保护你的电脑", "windows protected", "smartscreen", "windows 提示", "windows提示", "windows 拦截", "windows拦截"]},
    {"id": "install_mac", "line": "software", "os": "macos", "card_key": "support.known.install_mac",
     "triggers": ["无法验证开发者", "无法打开,因为", "cannot be verified", "unidentified developer", "mac 提示", "mac提示", "macos 提示", "macos提示"]},
]

from . import defaults_escalation as _de
from . import defaults_playbooks as _dp
from . import defaults_intake as _di

DEFAULTS: dict = {
    "intake.flows": _di.INTAKE_FLOWS,
    "intake.line_flow": _di.LINE_FLOW,
    "intake.max_questions": _di.MAX_QUESTIONS,
    "intake.budget_bands": _di.BUDGET_BANDS,
    "intake.side_answers": _di.SIDE_ANSWERS,
    "intake.cny_per_u": _di.CNY_PER_U,
    "intake.llm_extract.enabled": False,
    "intake.llm_extract.daily_cap": 300,
    "escalation.large_deal_u": _di.LARGE_DEAL_U,
    "playbooks": _dp.PLAYBOOKS,
    "cta.rules": _dp.CTA_RULES,
    "memory.retention_days": 90,
    "escalation.enabled": True,
    "escalation.rules_default": _de.ESCALATION_RULES,
    "escalation.explicit_human.min_ai_turns": 0,
    "sla.business_hours": _de.SLA_BUSINESS_HOURS,
    "sla.minutes": _de.SLA_MINUTES,
    "sla.owner_alert_after": _de.SLA_OWNER_ALERT_AFTER,
    "notify.routes": _de.NOTIFY_ROUTES,
    "trust.official_accounts": OFFICIAL_ACCOUNTS,
    "trust.official_addresses": [],
    "entry.intents": ENTRY_INTENTS,
    "experience.voice.business_enabled": False,
    "quote.confirm_above_u": 500,
    "quote.agent_custom_max_u": 1000,
    "payments.tolerance": {"abs_u": "1", "pct": "1"},
    "payments.quote_valid_hours": 24,
    "kb.known_issues": DEFAULT_KNOWN_ISSUES,
}


