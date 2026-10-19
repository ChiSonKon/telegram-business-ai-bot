"""升级矩阵(03 §6):有序规则,命中即转交真人;并计算对客户诚实的等待时间。

evaluate() 是纯函数:输入当前消息的意图/抽取结果/订单状态,输出 Escalation 或 None。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..config import cfg, copy
from .intake import engine, extract_rules as er

LOCKED_IDS = ("payment_issue", "refund", "paid_but_broken")
CANCEL_WORDS = ("算了", "不用了", "没事了", "取消", "不需要了", "never mind", "nevermind", "cancel", "forget it", "no thanks")


@dataclass
class Escalation:
    rule_id: str
    reason: str
    prio: str
    ask_first: str | None
    card: str


@dataclass
class Ctx:
    intent: str
    text: str = ""
    ex: dict = field(default_factory=dict)
    line: str | None = None
    slots: dict = field(default_factory=dict)
    signals: dict = field(default_factory=dict)
    has_open_order: bool = False
    has_paid_order: bool = False
    complaints: int = 0
    in_ai_stage: bool = False


# ---------- 规则表 ----------
def rules() -> list[dict]:
    """默认规则表 + 配置覆盖(enabled/prio/ask/card)。锁定项强制启用、不可删除。"""
    defaults = cfg.get("escalation.rules_default", default=[])
    override = {r.get("id"): r for r in (cfg.get("escalation.rules", default=None) or []) if isinstance(r, dict)}
    out = []
    for d in defaults:
        r = dict(d)
        o = override.get(d["id"])
        if o:
            for k in ("enabled", "prio", "ask", "card"):
                if k in o:
                    r[k] = o[k]
        if d.get("locked"):
            r["enabled"], r["locked"] = True, True
            if r.get("prio") != "P0":
                r["prio"] = "P0"
        out.append(r)
    return out


def enabled() -> bool:
    return bool(cfg.get("escalation.enabled", default=True))


def _units(ex: dict) -> float:
    m = ex.get("money")
    return er.to_units(m, float(cfg.get("intake.cny_per_u", default=7.0))) if m else 0.0


def detect_issue(text: str) -> str | None:
    low = (text or "").lower()
    for code, kws in engine.ISSUE_KW.items():
        if any(k in low for k in kws):
            return code
    return None


def _match(rule_id: str, c: Ctx) -> bool:
    issue = c.slots.get("issue") or (detect_issue(c.text) if c.intent in ("support", "payment_issue", "refund") else None)
    if rule_id == "payment_issue":
        return c.intent == "payment_issue" or issue == "payment" and c.intent == "support"
    if rule_id == "refund":
        return c.intent == "refund" or (issue == "refund" and c.intent == "support")
    if rule_id == "paid_but_broken":
        return c.intent == "support" and (issue == "license" or (c.has_paid_order and issue not in ("install", "renew", "payment", "refund")))
    if rule_id == "angry_paid_customer":
        return c.intent == "angry" and c.has_paid_order
    if rule_id == "large_deal":
        line_big = c.line in ("bot_custom", "network")
        return (_units(c.ex) >= float(cfg.get("escalation.large_deal_u", default=2000)) or c.slots.get("budget") == "2000+"
                or (bool(c.ex.get("company") or c.signals.get("company")) and line_big))
    if rule_id == "bulk":
        return c.line == "network" and ((c.ex.get("qty") or 0) >= 100 or c.slots.get("qty") == "100+")
    if rule_id == "explicit_human":
        return c.intent == "human_request"
    if rule_id == "ai_stuck":
        return c.in_ai_stage and c.complaints >= 2
    return False


def evaluate(c: Ctx) -> Escalation | None:
    if not enabled():
        return None
    for r in rules():
        if not r.get("enabled", True) or not _match(r["id"], c):
            continue
        ask = r.get("ask")
        if r["id"] == "payment_issue" and c.has_open_order:
            ask = None
        if r["id"] == "large_deal" and len("".join((c.text or "").split())) >= 10:
            ask = None
        if r["id"] == "explicit_human" and c.has_paid_order and r["prio"] == "P2":
            r = {**r, "prio": "P1"}                          # 有历史订单的客户要真人:提高一级
        return Escalation(r["id"], r["reason"], r["prio"], ask, r["card"])
    return None


# ---------- 等待时间 ----------
def _local(now: datetime | None) -> datetime:
    from whitecat_core.runtime import now as rt_now
    off = int(cfg.get("sla.business_hours", default={}).get("utc_offset", 8))
    return (now or rt_now()).astimezone(timezone(timedelta(hours=off)))


def sla_minutes(prio: str) -> int:
    return int(cfg.get("sla.minutes", default={}).get(prio, 120))


def in_business_hours(now: datetime | None = None) -> bool:
    bh = cfg.get("sla.business_hours", default={})
    h = _local(now).hour
    return int(bh.get("start", 9)) <= h < int(bh.get("end", 24))


def eta_text(prio: str, lang: str, now: datetime | None = None) -> str:
    """03 §6.2:工作时间 → "通常 N 分钟内";夜间 → "上午 H 点前"(P0 夜间 → 已加急)。"""
    bh = cfg.get("sla.business_hours", default={})
    if in_business_hours(now):
        return copy("eta.day", lang, n=sla_minutes(prio))
    h = int(bh.get("start", 9))
    return copy("eta.night_p0" if prio == "P0" else "eta.night", lang, h=h)


def is_cancel(text: str) -> bool:
    low = (text or "").strip().lower().strip("!！。. ")
    return any(low == w or low.startswith(w) for w in CANCEL_WORDS)
