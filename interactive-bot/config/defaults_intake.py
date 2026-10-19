"""问答流程默认值(03 §5.2)。槽位定义为数据,管理员可在设置中心编辑(WP6)。

slot 字段:key / kind(choice|text|band) / ask / hint / label / options[{code,zh,en}] / required / skippable /
          when(命名条件,见 engine.CONDITIONS)/ extract(命名抽取器,见 engine.EXTRACTORS)/ anytext(文本题不做问句判断)
flow 字段:role / line / slots / exit(ai | handoff:<reason>)/ prio(handoff 默认优先级)/ confirm(是否出确认卡)
"""
from __future__ import annotations

MAX_QUESTIONS = 3
LARGE_DEAL_U = 2000
CNY_PER_U = 7.0

BUDGET_BANDS = [
    {"code": "<300", "lo": 0, "hi": 300, "zh": "300 U 以内", "en": "Under 300 U"},
    {"code": "300-800", "lo": 300, "hi": 800, "zh": "300–800 U", "en": "300–800 U"},
    {"code": "800-2000", "lo": 800, "hi": 2000, "zh": "800–2000 U", "en": "800–2,000 U"},
    {"code": "2000+", "lo": 2000, "hi": None, "zh": "2000 U 以上", "en": "Over 2,000 U"},
    {"code": "declined", "zh": "先不说", "en": "Rather not say"},
]


def _opts(*rows):
    return [{"code": c, "zh": zh, "en": en} for c, zh, en in rows]


SLOT_LINE = {
    "key": "line", "kind": "choice", "required": True, "skippable": False, "dynamic": "lines",
    "ask": {"zh": "您想了解哪一类服务?", "en": "Which kind of service are you interested in?"},
    "hint": {"zh": "点选最接近的一项,或直接用一句话告诉我。", "en": "Tap the closest one, or just tell me in a sentence."},
    "label": {"zh": "业务", "en": "Service"}, "extract": ["line"],
}

INTAKE_FLOWS = {
    # ----- 入口:先选业务线,选定后切换到该线的流程 -----
    "prospect": {"role": "prospect", "slots": [SLOT_LINE], "exit": "ai", "confirm": False},

    # software:价目卡/推荐卡由规则渲染,多数客户不进入问答;horizon/devices/os 仅在"帮我选套餐"与售后安装场景使用(见 answers.py / WP9)
    "software": {"role": "prospect", "line": "software", "exit": "ai", "confirm": False, "slots": []},

    "bot_custom": {"role": "prospect", "line": "bot_custom", "exit": "ai", "confirm": True, "slots": [
        {"key": "need", "kind": "text", "required": True, "skippable": False, "extract": ["need"],
         "ask": {"zh": "想让机器人帮您做什么?一句话就够。", "en": "What should the bot do for you? One sentence is plenty."},
         "hint": {"zh": "", "en": ""}, "label": {"zh": "需求", "en": "Goal"}},
        {"key": "key_feature", "kind": "text", "required": False, "skippable": True, "extract": [],
         "ask": {"zh": "最重要的一个功能是什么?", "en": "What's the single most important feature?"},
         "hint": {"zh": "不方便回答可以跳过。", "en": "Feel free to skip."}, "label": {"zh": "核心功能", "en": "Key feature"}},
        {"key": "deadline", "kind": "choice", "required": False, "skippable": True, "extract": ["deadline"],
         "ask": {"zh": "希望什么时候用上?", "en": "When do you need it by?"}, "hint": {"zh": "", "en": ""},
         "label": {"zh": "期望时间", "en": "Timeline"},
         "options": _opts(("week", "1 周内", "Within a week"), ("month", "1 个月内", "Within a month"), ("none", "不着急", "No rush"))},
        {"key": "budget", "kind": "band", "required": False, "skippable": False, "when": "budget_unless_urgent_or_known", "extract": ["budget"],
         "ask": {"zh": "预算大概在哪个区间?", "en": "Roughly what budget do you have in mind?"},
         "hint": {"zh": "同样的功能有轻量版和完整版做法,知道区间能给您更合适的方案。不方便说可以跳过,不影响报价。",
                  "en": "Most features can be built lean or full-featured — a range helps us suggest the right one. Feel free to skip; it won't affect your quote."},
         "label": {"zh": "预算", "en": "Budget"}},
    ]},

    "network": {"role": "prospect", "line": "network", "exit": "handoff:quote_request", "prio": "P2", "confirm": False, "slots": [
        {"key": "ip_type", "kind": "choice", "required": True, "skippable": False, "extract": ["ip_type"],
         "ask": {"zh": "需要哪种 IP?", "en": "Which type of IP?"}, "hint": {"zh": "", "en": ""}, "label": {"zh": "IP 类型", "en": "IP type"},
         "options": _opts(("residential", "住宅", "Residential"), ("datacenter", "机房", "Datacenter"), ("unsure", "不确定", "Not sure"))},
        {"key": "qty", "kind": "choice", "required": True, "skippable": False, "extract": ["qty_band"],
         "ask": {"zh": "大概需要多少个?", "en": "Roughly how many?"}, "hint": {"zh": "", "en": ""}, "label": {"zh": "数量", "en": "Quantity"},
         "options": _opts(("1-10", "1–10", "1–10"), ("10-100", "10–100", "10–100"), ("100+", "100 以上", "100+"))},
        {"key": "duration", "kind": "choice", "required": True, "skippable": False, "extract": ["duration"],
         "ask": {"zh": "使用多久?", "en": "For how long?"}, "hint": {"zh": "", "en": ""}, "label": {"zh": "时长", "en": "Duration"},
         "options": _opts(("1m", "1 个月", "1 month"), ("3m", "3 个月", "3 months"), ("longer", "更长", "Longer"))},
    ]},

    "ai_account": {"role": "prospect", "line": "ai_account", "exit": "handoff:quote_request", "prio": "P2", "confirm": False, "slots": [
        {"key": "product", "kind": "choice", "required": True, "skippable": False, "extract": ["ai_product"],
         "ask": {"zh": "想要哪一款?", "en": "Which one are you after?"}, "hint": {"zh": "", "en": ""}, "label": {"zh": "产品", "en": "Product"},
         "options": _opts(("chatgpt_plus", "ChatGPT Plus", "ChatGPT Plus"), ("chatgpt_pro", "ChatGPT Pro", "ChatGPT Pro"),
                          ("gemini", "Gemini", "Gemini"), ("claude", "Claude", "Claude"), ("other", "其他", "Other"))},
        {"key": "qty", "kind": "choice", "required": True, "skippable": False, "extract": ["qty_band_small"],
         "ask": {"zh": "需要几个?", "en": "How many?"}, "hint": {"zh": "", "en": ""}, "label": {"zh": "数量", "en": "Quantity"},
         "options": _opts(("1", "1 个", "1"), ("2-5", "2–5 个", "2–5"), ("more", "更多", "More"))},
    ]},

    "other_biz": {"role": "prospect", "line": "other_biz", "exit": "ai", "confirm": False, "slots": [
        {"key": "need", "kind": "text", "required": True, "skippable": False, "extract": ["need"],
         "ask": {"zh": "具体想办理什么?一句话就够。", "en": "What exactly do you need? One sentence is enough."},
         "hint": {"zh": "预算可以稍后再说。", "en": "Budget can wait."}, "label": {"zh": "需求", "en": "Goal"}},
    ]},

    "generic": {"role": "prospect", "line": None, "exit": "ai", "confirm": False, "slots": [
        {"key": "need", "kind": "text", "required": True, "skippable": False, "extract": ["need"],
         "ask": {"zh": "具体想解决什么问题?", "en": "What are you trying to solve?"},
         "hint": {"zh": "一句话就够了,预算可以稍后再说。", "en": "One sentence is enough — budget can wait."}, "label": {"zh": "需求", "en": "Goal"}},
    ]},

    "sensitive": {"role": "prospect", "line": None, "exit": "handoff:sensitive_line", "prio": "P2", "confirm": False, "slots": [
        {"key": "need", "kind": "text", "required": True, "skippable": False, "anytext": True, "extract": [],
         "ask": {"zh": "请用一句话说明目标平台和需求。", "en": "Tell me the platform and what you need in one sentence."},
         "hint": {"zh": "", "en": ""}, "label": {"zh": "需求", "en": "Request"}},
    ]},

    "support": {"role": "support", "line": None, "exit": "ai", "confirm": False, "slots": [
        {"key": "product", "kind": "choice", "required": True, "skippable": False, "dynamic": "lines_with_other", "extract": ["line"],
         "ask": {"zh": "是哪一项产品或服务?", "en": "Which product or service is this about?"}, "hint": {"zh": "", "en": ""},
         "label": {"zh": "产品", "en": "Product"}},
        {"key": "issue", "kind": "choice", "required": True, "skippable": False, "extract": ["issue"],
         "ask": {"zh": "遇到的是哪类问题?", "en": "What kind of issue is it?"}, "hint": {"zh": "", "en": ""},
         "label": {"zh": "问题类型", "en": "Issue type"},
         "options": _opts(("install", "安装与使用", "Install & usage"), ("license", "授权与设备", "License & devices"),
                          ("payment", "付款未到账", "Payment not received"), ("delivery", "交付进度", "Delivery status"),
                          ("renew", "续费升级", "Renew / upgrade"), ("refund", "退款与争议", "Refund & dispute"), ("other", "其他", "Other"))},
        {"key": "desc", "kind": "text", "required": True, "skippable": False, "skippable_when_issue": ["install", "renew"], "extract": ["desc"],
         "ask": {"zh": "简单描述一下情况(报错文字可以直接粘贴)。", "en": "Briefly describe what's happening (you can paste any error text)."},
         "hint": {"zh": "没有订单号也没关系;有的话也可以一并发来。", "en": "An order number is optional — add it if you have it."},
         "label": {"zh": "描述", "en": "Description"}},
    ]},

    "partner": {"role": "partner", "line": None, "exit": "handoff:partner_intake", "prio": "P1", "confirm": False, "slots": [
        {"key": "ptype", "kind": "choice", "required": True, "skippable": False, "extract": ["ptype"],
         "ask": {"zh": "您想探讨哪种合作?", "en": "What kind of partnership do you have in mind?"}, "hint": {"zh": "", "en": ""},
         "label": {"zh": "合作类型", "en": "Partnership"},
         "options": _opts(("channel", "软件渠道", "Software reseller"), ("oem", "OEM / 品牌定制", "OEM / white label"),
                          ("project", "项目协作", "Project collaboration"), ("supply", "资源供应", "Resource supply"), ("other", "其他商务", "Other business"))},
        {"key": "scale", "kind": "text", "required": True, "skippable": False, "extract": [],
         "ask": {"zh": "大概的规模或资源,一句话。", "en": "Roughly your scale or resources, in a sentence."},
         "hint": {"zh": "例如分成、买断、联合运营,简单说说即可。", "en": "Revenue share, buyout, joint operation — a short note is fine."},
         "label": {"zh": "规模/资源", "en": "Scale / resources"}},
    ]},

    "visitor": {"role": "visitor", "line": None, "exit": "handoff:visitor_message", "prio": "P3", "confirm": False, "slots": [
        {"key": "message", "kind": "text", "required": True, "skippable": False, "anytext": True, "extract": [],
         "ask": {"zh": "请直接写下您的留言。", "en": "Please write your message here."},
         "hint": {"zh": "我们的同事看到后会在这里回复您,无需重复提交。", "en": "A teammate will reply in this chat — no need to resend."},
         "label": {"zh": "留言", "en": "Message"}},
    ]},
}

# 业务线 → 流程
LINE_FLOW = {"software": "software", "bot_custom": "bot_custom", "network": "network", "ai_account": "ai_account", "other_biz": "other_biz"}

# 旁问规则答复(命中即先答再回到当前题)。{kw: 触发词, zh/en: 答复}。答复只引用知识库已有口径。
SIDE_ANSWERS = [
    {"kw": ["多久", "什么时候能", "几天", "交期", "周期", "多快", "how long", "how soon", "turnaround", "delivery time"],
     "lines": ["bot_custom"],
     "zh": "交期要看功能范围:官网的“最快 3 小时”只适用于简单需求,具体交期以同事出具的报价单为准。",
     "en": "Timelines depend on scope: the “as fast as 3 hours” on our site only applies to simple requests — the exact date is on the written quote."},
    {"kw": ["多少钱", "价格", "报价", "费用", "how much", "price", "cost"], "lines": ["bot_custom"],
     "zh": "定制从 288 U 起,按功能复杂度报价;最终金额以同事出具的报价单为准,谈好的价格不会随意上涨。",
     "en": "Custom builds start at 288 U and are priced by scope; the final amount is on the written quote, and an agreed price stays put."},
    {"kw": ["试用", "free trial", "trial"], "lines": ["software"],
     "zh": "新设备自带免费试用(时长与限制以软件内显示为准);也可以买 6.9 U / 3 天的尝鲜体验卡,每台设备限 1 次。",
     "en": "New devices include a free trial (duration and limits are shown in the app); there's also a 6.9 U / 3-day starter pass, once per device."},
    {"kw": ["系统", "windows", "mac", "支持哪些", "which os", "operating system"], "lines": ["software"],
     "zh": "支持 Windows 10/11 x64 与 macOS(Apple 芯片、Intel)。",
     "en": "Windows 10/11 x64 and macOS (Apple silicon and Intel) are supported."},
    {"kw": ["哪里买", "怎么买", "在哪买", "where to buy", "how to buy"], "lines": ["software"],
     "zh": "软件内购买页一键购买,卡密自动发放并激活;也可以通过官方购买机器人 @oxbaimaobot。",
     "en": "Buy from the Purchase page inside the app — the key is delivered and activated automatically; or use our official bot @oxbaimaobot."},
]
