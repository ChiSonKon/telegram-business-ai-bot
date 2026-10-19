"""控制台数据层:只读查询,返回普通 dict,便于渲染与测试。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from db.database import SessionMaker
from db.model import AdminAudit, AiUsageLedger, FrontdeskCase, FrontdeskMessage, Order, Outbox, User

from ..frontdesk import abuse, budget
from ..frontdesk.service import POLICY, ROLE_LIMITS
from ..outbox import reason_info

PRIO_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
SLA_MINUTES = {"P0": 10, "P1": 30, "P2": 120, "P3": 24 * 60}      # 超时未处理 → 提醒一次
TAKEOVER_STALE_HOURS = 12                                          # 接管后超过此时长仍未释放 → 提醒一次
ROLE_ZH = {"prospect": "咨询购买", "support": "售后", "partner": "合作", "visitor": "留言"}
STATE_ZH = {"PRECHECK": "预检中", "AI_SALES": "AI 咨询中", "AI_SUPPORT": "AI 售后中", "AI_PARTNER": "AI 合作接待中",
            "HUMAN_PENDING": "等待跟进", "HUMAN_ACTIVE": "已接管", "RESOLVED": "已解决", "FRONTDESK": "未选择来意"}


def _naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def minutes_since(dt: datetime | None) -> int | None:
    dt = _naive(dt)
    return None if dt is None else max(0, int((datetime.utcnow() - dt).total_seconds() // 60))


def fmt_wait(minutes: int | None) -> str:
    if minutes is None:
        return "-"
    if minutes < 60:
        return f"{minutes} 分钟"
    if minutes < 24 * 60:
        return f"{minutes // 60} 小时 {minutes % 60} 分"
    return f"{minutes // 1440} 天"


def _answers(case) -> dict:
    try:
        return json.loads(case.answers_json or "{}")
    except ValueError:
        return {}


def _display(u: User | None, uid: int) -> tuple[str, str]:
    if not u:
        return str(uid), ""
    name = " ".join(x for x in (u.first_name, u.last_name) if x) or str(uid)
    return name, (f"@{u.username}" if u.username else "")


def case_view(uid: int) -> dict | None:
    """单个客户的处理卡数据。"""
    with SessionMaker() as db:
        c = db.query(FrontdeskCase).filter_by(user_id=uid, business_owner_id=0).order_by(FrontdeskCase.id.desc()).first()
        u = db.query(User).filter(User.user_id == uid).first()
        if not c and not u:
            return None
        name, uname = _display(u, uid)
        role = c.role if c else None
        prio, reason_label = reason_info(c.handoff_reason or "") if c and c.handoff_reason else ("P2", "")
        used = 0
        if role in ROLE_LIMITS:
            window = datetime.utcnow() - timedelta(hours=POLICY["quotas"]["rolling_window_hours"])
            used = db.query(func.coalesce(func.sum(AiUsageLedger.reply_units), 0)).filter(
                AiUsageLedger.user_id == uid, AiUsageLedger.role == role,
                AiUsageLedger.event.in_(("success", "reserve", "grant")), AiUsageLedger.created_at >= window).scalar() or 0
        orders = db.query(Order).filter(Order.user_id == uid).order_by(Order.created_at.desc()).limit(3).all()
        orders = [{"id": o.id, "title": o.title, "state": o.state} for o in orders]
        a = _answers(c) if c else {}
        brief = {}
        if c and c.brief_json:
            try:
                brief = json.loads(c.brief_json) or {}
            except (TypeError, ValueError):
                brief = {}
        messages = db.query(FrontdeskMessage).filter(FrontdeskMessage.user_id == uid, FrontdeskMessage.case_id == c.id if c else FrontdeskMessage.case_id.is_(None)).order_by(FrontdeskMessage.id.desc()).limit(5).all()
        return {
            "uid": uid, "name": name, "username": uname,
            "state": c.state if c else "FRONTDESK", "role": role, "prio": prio, "reason": reason_label,
            "reason_code": c.handoff_reason if c else None,
            "wait_min": minutes_since((c.handoff_at or c.updated_at) if c else None),
            "summary": (c.summary or "") if c else "", "first": a.get("first") or (c.initial_message if c else "") or "",
            "line": a.get("line"), "thread": u.message_thread_id if u else None,
            "quota_used": used, "quota_limit": ROLE_LIMITS.get(role, 0), "orders": orders,
            "brief": brief, "messages": [{"direction": m.direction, "text": (m.text or "")[:80], "created_at": m.created_at} for m in reversed(messages)],
        }


def _latest_per_user(db, rows):
    """每位客户只算最新的一条 case:历史遗留的旧 case 不再让同一个客户重复出现在待跟进/接管列表里。"""
    if not rows:
        return rows
    ids = [r.user_id for r in rows]
    newest = dict(db.query(FrontdeskCase.user_id, func.max(FrontdeskCase.id)).filter(
        FrontdeskCase.user_id.in_(ids), FrontdeskCase.business_owner_id == 0).group_by(FrontdeskCase.user_id).all())
    return [r for r in rows if newest.get(r.user_id) == r.id]


def handoff_queue(urgent_only: bool = False, flt: str = "all", admin_id: int | None = None) -> list[dict]:
    """等待跟进的客户,按优先级、等待时长排序(最久的在前)。"""
    out = []
    with SessionMaker() as db:
        state = "HUMAN_ACTIVE" if flt == "mine" else "HUMAN_PENDING"
        rows = db.query(FrontdeskCase).filter(FrontdeskCase.state == state, FrontdeskCase.business_owner_id == 0).all()
        rows = _latest_per_user(db, rows)
        users = {u.user_id: u for u in db.query(User).filter(User.user_id.in_([r.user_id for r in rows] or [0])).all()}
        for c in rows:
            prio, label = reason_info(c.handoff_reason or "")
            prio = c.priority if c.priority in PRIO_ORDER else prio
            if urgent_only and prio not in ("P0", "P1"):
                continue
            code = c.handoff_reason or ""
            if flt == "quote" and not code.startswith("quote_request"):
                continue
            if flt == "mine":
                last = db.query(AdminAudit).filter(AdminAudit.target == str(c.user_id), AdminAudit.action == "接管").order_by(AdminAudit.id.desc()).first()
                if not last or last.admin_id != admin_id:
                    continue
            name, uname = _display(users.get(c.user_id), c.user_id)
            at = _naive(c.handoff_at or c.updated_at)
            out.append({"key": f"{c.id}:{int(at.timestamp()) if at else 0}", "uid": c.user_id, "name": name, "username": uname, "prio": prio, "reason": label, "reason_code": c.handoff_reason or "", "role": c.role,
                        "wait_min": minutes_since(c.handoff_at or c.updated_at) or 0})
    out.sort(key=lambda x: (PRIO_ORDER[x["prio"]], -x["wait_min"]))
    return out


def active_takeovers() -> list[dict]:
    out = []
    with SessionMaker() as db:
        for c in _latest_per_user(db, db.query(FrontdeskCase).filter(FrontdeskCase.state == "HUMAN_ACTIVE", FrontdeskCase.business_owner_id == 0).all()):
            u = db.query(User).filter(User.user_id == c.user_id).first()
            at = _naive(c.updated_at)
            out.append({"key": f"{c.id}:{int(at.timestamp()) if at else 0}", "uid": c.user_id, "name": _display(u, c.user_id)[0],
                        "since_min": minutes_since(c.updated_at) or 0})
    return sorted(out, key=lambda x: -x["since_min"])


def orders_overview() -> dict:
    with SessionMaker() as db:
        def rows(states, limit=8):
            q = db.query(Order).filter(Order.state.in_(states)).order_by(Order.updated_at.desc()).limit(limit).all()
            return [{"id": o.id, "uid": o.user_id, "title": o.title, "state": o.state, "wait_min": minutes_since(o.updated_at) or 0} for o in q]
        return {"review": rows(("payment_review",)), "todo": rows(("paid", "fulfilling")), "waiting": rows(("awaiting_payment",), 5),
                "recent": rows(("delivered",), 5)}


def order_card(order_id: str) -> dict | None:
    with SessionMaker() as db:
        o = db.get(Order, order_id)
        if not o:
            return None
        u = db.query(User).filter(User.user_id == o.user_id).first()
        return {"id": o.id, "uid": o.user_id, "name": _display(u, o.user_id)[0], "title": o.title, "state": o.state,
                "stars": o.stars, "ton_nano": o.ton_nano, "age_min": minutes_since(o.created_at),
                "txid": o.paid_txid, "network": o.network, "amount": o.amount_str, "currency": o.currency,
                "source_case_id": o.source_case_id, "pay_address": o.pay_address,
                "created_at": o.created_at, "paid_at": getattr(o, "paid_at", None), "delivered_at": getattr(o, "delivered_at", None)}


def export_orders(month: str) -> list[dict]:
    """Return a stable, CSV-ready order projection for YYYY-MM."""
    try:
        year, mon = (int(x) for x in month.split("-", 1))
        start = datetime(year, mon, 1)
        end = datetime(year + (mon == 12), 1 if mon == 12 else mon + 1, 1)
    except (ValueError, TypeError):
        raise ValueError("月份格式应为 YYYY-MM")
    with SessionMaker() as db:
        rows = db.query(Order).filter(Order.created_at >= start, Order.created_at < end).order_by(Order.created_at.asc()).all()
        return [{"order_id": o.id, "customer_id": o.user_id, "title": o.title, "amount": o.amount_str or (str(o.stars) if o.stars else str(o.ton_nano / 1e9) if o.ton_nano else ""),
                 "network": o.network or ("TON" if o.ton_nano else "Stars"), "txid": o.paid_txid or "", "status": o.state,
                 "created_at": o.created_at, "paid_at": o.paid_at, "delivered_at": o.delivered_at,
                 "operator": o.created_by or ""} for o in rows]


def _day_start() -> datetime:
    return budget._day_start_utc()


def today_stats(days_back: int = 0) -> dict:
    start = _day_start() - timedelta(days=days_back)
    end = start + timedelta(days=1)
    with SessionMaker() as db:
        def count(model_col, *conds):
            return db.query(func.count(model_col)).filter(*conds).scalar() or 0
        new_cases = count(FrontdeskCase.id, FrontdeskCase.created_at >= start, FrontdeskCase.created_at < end)
        prechecked = count(FrontdeskCase.id, FrontdeskCase.updated_at >= start, FrontdeskCase.updated_at < end,
                           FrontdeskCase.answers_json.like('%"done": true%'))
        replies = count(AiUsageLedger.id, AiUsageLedger.event == "success", AiUsageLedger.created_at >= start, AiUsageLedger.created_at < end)
        cost = db.query(func.coalesce(func.sum(AiUsageLedger.cost_usd), 0.0)).filter(
            AiUsageLedger.created_at >= start, AiUsageLedger.created_at < end).scalar() or 0.0
        paid = count(Order.id, Order.state.in_(("paid", "fulfilling", "delivered")), Order.updated_at >= start, Order.updated_at < end)
        handoffs = count(FrontdeskCase.id, FrontdeskCase.handoff_at >= start, FrontdeskCase.handoff_at < end)
        strikes = count(AiUsageLedger.id, AiUsageLedger.event == "strike", AiUsageLedger.created_at >= start, AiUsageLedger.created_at < end)
    return {"new": new_cases, "prechecked": prechecked, "replies": replies, "cost": float(cost), "paid": paid,
            "handoffs": handoffs, "strikes": strikes}


def period(days: int, end_offset_days: int = 0) -> tuple[datetime, datetime]:
    """以当地(UTC+8)自然日为界的 [since, until)(返回 naive UTC,与库内时间一致)。"""
    until = _day_start() + timedelta(days=1) - timedelta(days=end_offset_days)
    return until - timedelta(days=days), until


def funnel_summary(days: int = 1, end_offset_days: int = 0) -> dict:
    from ..analytics import funnel
    since, until = period(days, end_offset_days)
    f = funnel.funnel(since, until)
    return {"funnel": f, "dropoffs": funnel.dropoffs(since, until), "sources": funnel.by_source(since, until),
            "handoffs": funnel.handoff_stats(since, until), "since": since, "until": until}


def funnel_line(days: int = 1, end_offset_days: int = 0) -> str:
    f = funnel_summary(days, end_offset_days)["funnel"]
    return (f"进线 {f['F1']} → 有用回应 {f['F2']} → 完成问答 {f['F3']} → AI 有效对话 {f['F4']} → 报价/入口 {f['F5']} → 付款 {f['F7']}")


def pending_counts() -> dict:
    """摘要用:P0 数、待报价数、24 小时内冒充报告数。"""
    from db.model import FunnelEvent
    queue = handoff_queue()
    since = datetime.utcnow() - timedelta(hours=24)
    with SessionMaker() as db:
        imp = db.query(func.count(FunnelEvent.id)).filter(FunnelEvent.event == "impersonation_report", FunnelEvent.ts >= since,
                                                         FunnelEvent.test.is_(False)).scalar() or 0
    return {"p0": sum(1 for x in queue if x["prio"] == "P0"),
            "quote": sum(1 for x in queue if (x.get("reason_code") or "").startswith("quote_request")), "impersonation": imp}


def week_series() -> list[dict]:
    return [today_stats(i) for i in range(6, -1, -1)]


def health() -> dict:
    from whitecat_core.runtime import llm_breaker
    from ..commerce import service as commerce
    limit_hit = budget.exhausted()
    with SessionMaker() as db:
        backlog = db.query(func.count(Outbox.id)).filter(Outbox.sent.is_(False)).scalar() or 0
        failing = db.query(func.count(Outbox.id)).filter(Outbox.sent.is_(False), Outbox.attempts >= 3).scalar() or 0
    cfg = commerce.get_ton_config()
    return {
        "breaker": llm_breaker.state, "limit_hit": limit_hit, "priced": budget.prices() is not None,
        "budget": budget.daily_budget(), "backlog": backlog, "failing": failing,
        "ton": bool(cfg.get("address")), "catalog": len(commerce.get_catalog()),
    }


def dashboard() -> dict:
    queue = handoff_queue()
    ov = orders_overview()
    pending = pending_counts()
    alerts = []
    h = health()
    if h["breaker"] == "open":
        alerts.append("AI 熔断已打开")
    if h["budget"] and today_stats()["cost"] >= h["budget"] * 0.8:
        alerts.append("AI 预算已达 80%")
    if pending["impersonation"]:
        alerts.append(f"冒充报告 {pending['impersonation']} 条")
    return {
        "queue": queue, "p0": sum(1 for x in queue if x["prio"] == "P0"), "oldest": max((x["wait_min"] for x in queue), default=0),
        "takeovers": active_takeovers(), "orders": ov, "today": today_stats(), "yesterday": today_stats(1), "health": h,
        "pending": pending, "alerts": alerts,
    }


def audit_log(limit: int = 15) -> list[dict]:
    with SessionMaker() as db:
        rows = db.query(AdminAudit).order_by(AdminAudit.id.desc()).limit(limit).all()
        return [{"admin": r.admin_id, "action": r.action, "target": r.target, "detail": r.detail,
                 "min": minutes_since(r.created_at)} for r in rows]


def find_user(text: str) -> int | None:
    """按数字 ID 或 @用户名 查找客户。"""
    text = (text or "").strip()
    with SessionMaker() as db:
        if text.lstrip("-").isdigit():
            uid = int(text)
            return uid if db.query(User).filter(User.user_id == uid).first() or db.query(FrontdeskCase).filter_by(user_id=uid).first() else None
        name = text.lstrip("@").lower()
        u = db.query(User).filter(func.lower(User.username) == name).first()
        return u.user_id if u else None


def abuse_flags(limit: int = 5) -> list[dict]:
    """近 1 小时 strike 最多的客户(管理员可据此判断是否有人在刷)。"""
    since = datetime.utcnow() - timedelta(minutes=abuse.STRIKE_WINDOW_MIN)
    with SessionMaker() as db:
        rows = db.query(AiUsageLedger.user_id, func.count(AiUsageLedger.id)).filter(
            AiUsageLedger.event == "strike", AiUsageLedger.created_at > since).group_by(AiUsageLedger.user_id).order_by(
            func.count(AiUsageLedger.id).desc()).limit(limit).all()
    return [{"uid": r[0], "strikes": r[1]} for r in rows]
