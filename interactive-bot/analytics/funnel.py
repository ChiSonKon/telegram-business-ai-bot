"""漏斗与卡点查询。所有查询排除 test=True(客户视角模式)的事件。"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy import func

from db.database import SessionMaker
from db.model import FrontdeskCase, FunnelEvent

# F1–F8:(名称, 事件集合)
STAGES = [
    ("F1 进线", ("entry",)),
    ("F2 有用回应", ("first_value",)),
    ("F3 完成问答", ("intake_completed",)),
    ("F4 AI 有效对话", ("ai_effective",)),
    ("F5 报价/入口", ("quote_sent", "cta_clicked")),
    ("F6 点击付款", ("pay_clicked",)),
    ("F7 付款确认", ("payment_verified",)),
    ("F8 已交付", ("delivered",)),
]


def _base(db, since: datetime, until: datetime):
    return db.query(FunnelEvent).filter(FunnelEvent.ts >= since, FunnelEvent.ts < until, FunnelEvent.test.is_(False))


def funnel(since: datetime, until: datetime) -> dict:
    """每阶段去重客户数与相对 F1 的转化率。"""
    stages = []
    with SessionMaker() as db:
        for name, events in STAGES:
            n = _base(db, since, until).filter(FunnelEvent.event.in_(events)).with_entities(
                func.count(func.distinct(FunnelEvent.user_id))).scalar() or 0
            stages.append({"name": name, "count": n})
    top = stages[0]["count"] or 0
    for s in stages:
        s["rate"] = (s["count"] / top) if top else 0.0
    return {"stages": stages, **{f"F{i + 1}": s["count"] for i, s in enumerate(stages)}}


def dropoffs(since: datetime, until: datetime) -> list[tuple]:
    """(flow, slot, shown, answered, drop_rate),按流失率降序。answered 含主动跳过。"""
    shown, answered = defaultdict(int), defaultdict(int)
    with SessionMaker() as db:
        rows = _base(db, since, until).filter(FunnelEvent.event.in_(("intake_step_shown", "intake_answered", "intake_skipped"))).all()
        for r in rows:
            props = json.loads(r.props_json or "{}")
            key = (props.get("flow") or r.line or "-", props.get("slot") or "-")
            (shown if r.event == "intake_step_shown" else answered)[key] += 1
    out = []
    for key, s in shown.items():
        a = min(answered.get(key, 0), s)
        out.append((key[0], key[1], s, a, (s - a) / s if s else 0.0))
    return sorted(out, key=lambda x: (-x[4], -x[2]))


def by_source(since: datetime, until: datetime) -> list[dict]:
    with SessionMaker() as db:
        rows = _base(db, since, until).filter(FunnelEvent.event == "entry").with_entities(
            FunnelEvent.source, func.count(func.distinct(FunnelEvent.user_id))).group_by(FunnelEvent.source).all()
        converted = dict(_base(db, since, until).filter(FunnelEvent.event.in_(("quote_sent", "cta_clicked", "payment_verified"))).with_entities(
            FunnelEvent.source, func.count(func.distinct(FunnelEvent.user_id))).group_by(FunnelEvent.source).all())
    out = [{"source": r[0] or "未知", "entries": r[1], "converted": converted.get(r[0], 0)} for r in rows]
    return sorted(out, key=lambda x: -x["entries"])


def handoff_stats(since: datetime, until: datetime) -> dict:
    """按原因/优先级的转交数,以及 human_first_reply 的中位等待(分钟)。"""
    reasons, prios, waits = defaultdict(int), defaultdict(int), []
    with SessionMaker() as db:
        for r in _base(db, since, until).filter(FunnelEvent.event.in_(("handoff", "human_first_reply"))).all():
            props = json.loads(r.props_json or "{}")
            if r.event == "handoff":
                reasons[props.get("reason", "-")] += 1
                prios[props.get("prio", "-")] += 1
            elif "wait_min" in props:
                waits.append(float(props["wait_min"]))
    waits.sort()
    median = waits[len(waits) // 2] if waits else None
    return {"reasons": dict(reasons), "prios": dict(prios), "median_wait_min": median, "replied": len(waits)}
