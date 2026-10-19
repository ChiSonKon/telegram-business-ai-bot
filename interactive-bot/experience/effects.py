"""消息特效(03 §8.2):只用于"已收款"和"已交付"两个时刻,只在 Bot 私聊,同一客户 24 小时 1 次;失败自动降级重发。"""
from __future__ import annotations

import logging

from db.database import SessionMaker
from db.model import CustomerProfile

from ..config import cfg
from . import capabilities

logger = logging.getLogger(__name__)

DEFAULT_PARTY_ID = "5046509860389126442"
MOMENTS = {"order.paid": "experience.effects.paid_effect_id", "order.delivered": "experience.effects.delivered_effect_id"}
COOLDOWN_S = 24 * 3600


def _now() -> float:
    from whitecat_core.runtime import now
    return now().timestamp()


def effect_id(moment: str) -> str | None:
    key = MOMENTS.get(moment)
    if not key:
        return None
    return str(cfg.get(key, default=DEFAULT_PARTY_ID) or DEFAULT_PARTY_ID)


def effect_kwargs(user_id: int, moment: str, channel: str) -> dict:
    """允许时返回 {"message_effect_id": id},否则 {}。Business、非白名单时刻、冷却中、能力被关闭时一律不发。"""
    if channel != "bot" or moment not in MOMENTS:
        return {}
    if not cfg.get("experience.effects.enabled", default=True) or not capabilities.ok("bot", "effect"):
        return {}
    with SessionMaker() as db:
        p = db.get(CustomerProfile, user_id)
        if p and p.last_effect_at and _now() - p.last_effect_at < COOLDOWN_S:
            return {}
    eid = effect_id(moment)
    return {"message_effect_id": eid} if eid else {}


def mark_sent(user_id: int) -> None:
    from ..frontdesk import profile
    with SessionMaker() as db:
        p = profile._ensure(db, user_id)
        p.last_effect_at = _now()
        db.commit()


async def send(bot, chat_id: int, text: str, markup, *, user_id: int, moment: str, channel: str = "bot", biz: str | None = None):
    """发送带特效的通知;特效失败(BadRequest 含 effect)则去掉特效重发,并关闭该能力 24 小时。返回消息或 None。"""
    from ..ui import send_card
    kw = effect_kwargs(user_id, moment, channel)
    if kw:
        msg = await send_card(bot, chat_id, text, markup, biz, **kw)
        if msg is not None:
            mark_sent(user_id)
            _emit("effect_sent", user_id, kind=moment[:32])
            return msg
        capabilities.fail("bot", "effect")                    # 带特效发送失败:关闭 24 小时,不带特效重发
        logger.warning("message effect rejected; resending without effect")
    return await send_card(bot, chat_id, text, markup, biz)


def _emit(event: str, user_id: int, **props) -> None:
    try:
        from ..analytics.events import emit
        emit(event, user_id, **props)
    except Exception:
        pass
