"""前台入口门:Bot 私聊与 Business 共用。

WP1 起入口逻辑在 router.py(先答后问);本模块仅保留稳定的 admit 门面。
"""
from __future__ import annotations

from whitecat_core.runtime import lang_for

from ..ui import send_card
from . import precheck, router
from .cards import intent_text, welcome_text
from .router import Gate, advance, classify, RETURNING_AFTER_HOURS
from .service import case_state, get_role, handoff, idle_hours_before_current, in_precheck, route_intent, set_intent
from .texts import t

__all__ = ["Gate", "advance", "classify", "admit", "RETURNING_AFTER_HOURS"]


async def admit(bot, user_id: int, chat_id: int, text: str, *, channel: str, biz_conn_id: str | None = None) -> Gate:
    return await router.handle_customer_text(bot, user_id, chat_id, text, channel=channel, biz_conn_id=biz_conn_id)
