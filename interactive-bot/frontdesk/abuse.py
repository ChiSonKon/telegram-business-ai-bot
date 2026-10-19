"""滥用判定:只有"频繁超额调用 AI"才触发人机验证,正常客户和预检阶段永远不会遇到。

计分(都落在 AiUsageLedger,重启不丢):
- strike:被入口护盾丢弃(洪泛/重复刷屏)、被限流、额度耗尽后仍继续要求 AI;
- 突发:5 分钟内发起 ≥ 8 次 AI 回合。
任一达标(60 分钟内 ≥ 3 次 strike,或突发)即要求通过一次验证;通过后记 verified,窗口清零。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func

from db.database import SessionMaker
from db.model import AiUsageLedger

from ..global_settings import get_global_setting

STRIKES_TO_CHALLENGE = 3
STRIKE_WINDOW_MIN = 60
BURST_TURNS = 8
BURST_WINDOW_MIN = 5


def _now() -> datetime:
    return datetime.utcnow()


def _add(user_id: int, event: str, role: str = "") -> None:
    with SessionMaker() as db:
        db.add(AiUsageLedger(user_id=user_id, event=event, role=role, reply_units=0, reservation_key=f"{event}-{uuid.uuid4().hex}"))
        db.commit()


def strike(user_id: int, kind: str) -> None:
    _add(user_id, "strike", kind)


def mark_verified(user_id: int) -> None:
    _add(user_id, "verified")


def needs_challenge(user_id: int) -> bool:
    if get_global_setting("disable_captcha"):
        return False
    now = _now()
    with SessionMaker() as db:
        last = db.query(func.max(AiUsageLedger.created_at)).filter(
            AiUsageLedger.user_id == user_id, AiUsageLedger.event == "verified").scalar()
        floor = last if last and last.tzinfo is None else (last.replace(tzinfo=None) if last else None)

        def since(minutes: int) -> datetime:
            t = now - timedelta(minutes=minutes)
            return max(t, floor) if floor else t

        strikes = db.query(func.count(AiUsageLedger.id)).filter(
            AiUsageLedger.user_id == user_id, AiUsageLedger.event == "strike", AiUsageLedger.created_at > since(STRIKE_WINDOW_MIN)).scalar()
        turns = db.query(func.count(AiUsageLedger.id)).filter(
            AiUsageLedger.user_id == user_id, AiUsageLedger.event.in_(("reserve", "success", "release")),
            AiUsageLedger.created_at > since(BURST_WINDOW_MIN)).scalar()
    return strikes >= STRIKES_TO_CHALLENGE or turns >= BURST_TURNS
