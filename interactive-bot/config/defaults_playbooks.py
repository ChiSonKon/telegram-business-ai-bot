"""AI 引导剧本默认值(03 §7.2)。阶段判断由代码做(看简报槽位是否已填),模型只负责语言。

字段:goal / stages[{id, exit_when[], min_turns, instruction, action, say}] / must_not / objections / max_turns_before_close
action:"handoff:<reason>"(由代码在 AI 回复发出之后执行);say:进入 action 阶段时要求 AI 以此收尾的话。
"""
from __future__ import annotations

_COMMON_MUST_NOT = {
    "zh": ["给出未在业务数据中出现的价格或链接", "承诺效果、通过率或收益", "索要密码、验证码、助记词或私钥"],
    "en": ["quote any price or link that is not in the business data", "promise results, approval rates or returns", "ask for passwords, login codes, seed phrases or private keys"],
}


def _mn(zh_extra, en_extra):
    return {"zh": _COMMON_MUST_NOT["zh"] + zh_extra, "en": _COMMON_MUST_NOT["en"] + en_extra}


PLAYBOOKS = {
    "bot_custom": {
        "line": "bot_custom",
        "goal": {"zh": "弄清需求到可以出报价单的程度,然后交给同事报价",
                 "en": "Understand the request well enough for a written quote, then hand over to a teammate"},
        "stages": [
            {"id": "understand", "exit_when": ["need", "key_feature", "customer_channel"], "min_turns": 0,
             "instruction": {"zh": "复述客户目标;如果缺 customer_channel,问\"客户主要从哪里来找您\";简报里已有的信息不要再问",
                             "en": "Restate the customer's goal; if customer_channel is missing, ask where their customers mostly come from; never re-ask what the brief already has"}},
            {"id": "shape", "exit_when": ["scale"], "min_turns": 0,
             "instruction": {"zh": "给一个实现思路(轻量/完整两种做法),问规模(日均咨询量)",
                             "en": "Outline an approach (a lean and a full version) and ask about scale (daily enquiries)"}},
            {"id": "close", "action": "handoff:quote_request",
             "say": {"zh": "这些信息够出一份报价单了,我现在交给负责定制的同事,他会在这里给您报价。",
                     "en": "That's enough for a written quote — I'm passing this to our custom-build lead, who will send you the quote right here."}},
        ],
        "must_not": _mn(["给出具体价格或交期", "承诺\"最快3小时\"", "贬低同行", "再问已拒绝的预算"],
                        ["give a specific price or delivery date", "promise \"as fast as 3 hours\"", "disparage competitors", "ask again about a budget the customer declined"]),
        "objections": {
            "price_high": {"zh": "承认差异常见;说明报价由功能、稳定性和售后决定;可以先做核心功能分期交付;不承诺折扣",
                           "en": "Acknowledge that quotes vary; explain price follows scope, stability and after-sales; suggest delivering the core first; promise no discounts"},
            "competitor": {"zh": "不评价同行;建议客户比较交付物与售后条款",
                           "en": "Don't comment on competitors; suggest comparing deliverables and after-sales terms"},
            "trust": {"zh": "介绍公开承诺\"谈好的价格不随意上涨\"与报价单流程;可提醒发送 @用户名 让我核验官方账号",
                      "en": "Mention the public promise that an agreed price stays put and the written-quote process; remind them they can send me an @username to check an official account"},
        },
        "max_turns_before_close": 4,
    },
    "software": {
        "line": "software",
        "goal": {"zh": "帮客户选对档位并去官方入口购买(或先免费试用)", "en": "Help the customer pick the right plan and go to the official checkout (or try free first)"},
        "stages": [
            {"id": "answer", "exit_when": [], "min_turns": 1,
             "instruction": {"zh": "先如实回答功能问题(只说知识库里有的,不确定就说需要同事确认);了解用途和账号/设备量",
                             "en": "Answer feature questions honestly from the business data only; learn the use case and how many accounts/devices"}},
            {"id": "recommend", "exit_when": [], "min_turns": 2,
             "instruction": {"zh": "推荐 1 档套餐,并提醒新设备有免费试用;不要堆砌所有档位",
                             "en": "Recommend one plan and mention the free trial on new devices; don't list every plan"}},
            {"id": "close", "action": "cta:official_buy",
             "say": {"zh": "想直接开通的话,下面是官方购买入口。", "en": "If you'd like to go ahead, here is the official checkout."}},
        ],
        "must_not": _mn(["保证不封号或保证回复率", "承诺具体效果"], ["guarantee no bans or reply rates", "promise any specific outcome"]),
        "objections": {
            "price_high": {"zh": "对比月度/季度/年度的单月成本;提醒可以先用免费试用", "en": "Compare per-month cost across plans and remind them of the free trial"},
            "trust": {"zh": "说明可先试用再买;购买只走官方入口", "en": "Point out they can try before buying and that purchases only go through the official channels"},
        },
        "max_turns_before_close": 4,
    },
    "network": {
        "line": "network",
        "goal": {"zh": "回答 IP 常识问题,把数量和时长弄清楚后交给同事报价", "en": "Answer basic IP questions, clarify quantity and duration, then hand over for a quote"},
        "stages": [
            {"id": "understand", "exit_when": [], "min_turns": 1,
             "instruction": {"zh": "解释住宅与机房 IP 的差异、按用途匹配;库存和实时价格必须由同事确认,不要承诺库存",
                             "en": "Explain residential vs datacenter IPs and match them to the use; stock and live prices must be confirmed by a teammate — never promise stock"}},
            {"id": "close", "action": "handoff:quote_request",
             "say": {"zh": "库存和价格需要同事确认,我现在把您的需求交给他,他会在这里回复您。", "en": "Stock and pricing need a teammate's confirmation — I'm passing your request to them, and they'll reply here."}},
        ],
        "must_not": _mn(["承诺库存", "给出实时价格"], ["promise stock", "state live prices"]),
        "objections": {}, "max_turns_before_close": 3,
    },
    "ai_account": {
        "line": "ai_account",
        "goal": {"zh": "回答品类问题,确认数量后交给同事报价", "en": "Answer product questions, confirm quantity, then hand over for a quote"},
        "stages": [
            {"id": "understand", "exit_when": [], "min_turns": 1,
             "instruction": {"zh": "回答品类和用法问题;绝不涉及账号密码;售后范围以订单约定为准,不要扩大承诺",
                             "en": "Answer product and usage questions; never touch account passwords; after-sales scope follows the order terms — never widen it"}},
            {"id": "close", "action": "handoff:quote_request",
             "say": {"zh": "品类和库存需要同事确认,我现在交给他,他会在这里回复您。", "en": "Products and stock need a teammate's confirmation — I'm passing this on, and they'll reply here."}},
        ],
        "must_not": _mn(["扩大售后承诺"], ["widen the after-sales promise"]),
        "objections": {}, "max_turns_before_close": 3,
    },
    "support": {
        "line": "support",
        "goal": {"zh": "一步一步排查,解决不了就交给同事", "en": "Troubleshoot one step at a time; hand over if it isn't resolved"},
        "stages": [
            {"id": "troubleshoot", "exit_when": [], "min_turns": 2,
             "instruction": {"zh": "每轮只给一个排查步骤,等客户反馈;不要一次列出所有步骤", "en": "Give exactly one troubleshooting step per turn and wait for feedback; never list every step at once"}},
            {"id": "close", "action": "handoff:ai_stuck",
             "say": {"zh": "这个问题我先帮您转给同事进一步处理,记录他都能看到。", "en": "I'll pass this to a teammate for a closer look — they can see everything here."}},
        ],
        "must_not": _mn(["承诺退款", "索要完整卡密"], ["promise a refund", "ask for the full license key"]),
        "objections": {}, "max_turns_before_close": 2,
    },
}

CTA_RULES = [{"line": "software", "after_turn": 2, "card": "official_buy", "once_per_case": True}]
