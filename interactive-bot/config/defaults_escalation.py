"""升级矩阵、SLA、通知路由默认值(03 §6、04 §8)。

规则有序,命中第一条。P0 的 payment_issue / refund / paid_but_broken 为锁定项:不可删除、不可禁用。
rule 字段:id / reason(handoff 原因码)/ prio / enabled / locked / ask(先问一题的文案键,None=不问)/ card(转交确认卡文案键)
"""
from __future__ import annotations

ESCALATION_RULES = [
    {"id": "payment_issue", "reason": "support_escalation:payment", "prio": "P0", "locked": True, "enabled": True,
     "ask": "escalation.ask_payment", "card": "handoff.payment_issue"},
    {"id": "refund", "reason": "support_escalation:refund", "prio": "P0", "locked": True, "enabled": True,
     "ask": None, "card": "support.refund"},
    {"id": "paid_but_broken", "reason": "paid_but_broken", "prio": "P0", "locked": True, "enabled": True,
     "ask": "support.license.ask", "card": "handoff.paid_but_broken"},
    {"id": "angry_paid_customer", "reason": "angry_paid_customer", "prio": "P0", "locked": False, "enabled": True,
     "ask": None, "card": "care.angry_paid"},
    {"id": "large_deal", "reason": "large_deal", "prio": "P1", "locked": False, "enabled": True,
     "ask": "escalation.ask_need", "card": "handoff.large_deal"},
    {"id": "bulk", "reason": "quote_request", "prio": "P1", "locked": False, "enabled": True,
     "ask": None, "card": "handoff.quote_request"},
    {"id": "explicit_human", "reason": "explicit_human", "prio": "P2", "locked": False, "enabled": True,
     "ask": "human.ask", "card": "human.done"},
    {"id": "ai_stuck", "reason": "ai_stuck", "prio": "P2", "locked": False, "enabled": True,
     "ask": None, "card": "human.done"},
]

SLA_BUSINESS_HOURS = {"start": 9, "end": 24, "utc_offset": 8}       # 09:00–24:00 Asia/Shanghai
SLA_MINUTES = {"P0": 10, "P1": 30, "P2": 120, "P3": 1440}
SLA_OWNER_ALERT_AFTER = 10                                           # P0 超过此分钟数未被接手 → 私信业主

NOTIFY_ROUTES = {
    "default": {"chat": "admin_group", "topic": "customer"},
    "P0": {"chat": "admin_group", "topic": "customer", "also_dm_roles": ["owner"], "after_minutes": 10},
    "impersonation_report": {"chat": "admin_group", "topic": "general"},
    "payment_review": {"chat": "admin_group", "topic": "customer", "also_dm_roles": ["finance"]},
    "config_published": {"chat": "admin_group", "topic": "general"},
}
