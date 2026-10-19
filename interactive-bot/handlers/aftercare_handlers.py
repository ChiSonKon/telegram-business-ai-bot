"""售后按钮回调:已知问题卡(解决了/还是不行)、交付后回访、续费提醒。按钮只作用于点击者自己的会话与订单。"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from whitecat_core import runtime

from ..commerce import service
from ..config import copy
from ..experience import cleanup, tgx, ui_registry
from ..frontdesk import aftercare, precheck, router
from ..frontdesk.service import handoff

logger = logging.getLogger(__name__)


def _emit(event: str, user_id: int, **props) -> None:
    try:
        from ..analytics.events import emit
        emit(event, user_id, **props)
    except Exception:
        pass


async def _start_support(bot, uid: int, chat_id: int, lang: str, text: str) -> None:
    precheck.begin(uid, "support", text)
    await router.advance(bot, uid, chat_id, lang)


async def callback_aft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = (q.data or "").split(":", 2)
    if len(parts) < 3:
        await q.answer()
        return
    action, ref = parts[1], parts[2]
    uid = q.from_user.id
    chat_id = q.message.chat_id if q.message else uid
    lang = runtime.lang_for(uid)
    await q.answer()
    bot = context.bot

    if action in ("ok", "no"):
        ui = ui_registry.latest(uid, "known_issue", ref=ref)
        if not ui:                                                   # 旧卡/重复点击:静默
            return
        if action == "ok":
            await cleanup.collapse(bot, ui["id"], copy("support.resolved", lang))
            _emit("self_resolved", uid)
            router.mark_answered(uid, "bot", card="known_issue")
        else:
            await tgx.strip_buttons(bot, ui["chat_id"], ui["message_id"], ui["biz"])
            ui_registry.set_state(ui["id"], "collapsed")
            await _start_support(bot, uid, chat_id, lang, aftercare.recall(uid) or "售后问题")
        return

    order = service.get_order(ref)
    if not order or order.user_id != uid:                            # 他人的订单:静默忽略
        return
    ui = ui_registry.latest(uid, "checkin" if action in ("fine", "help") else "renew", ref=ref)
    if action == "fine":
        if ui:
            await cleanup.collapse(bot, ui["id"], copy("aftercare.thanks", lang))
    elif action == "help":
        if ui:
            await cleanup.collapse(bot, ui["id"], copy("aftercare.thanks_help", lang))
        await _start_support(bot, uid, chat_id, lang, f"订单 {order.id} {order.title or ''} 使用中有问题")
    elif action == "renew":
        if ui:
            await cleanup.collapse(bot, ui["id"], copy("aftercare.renew_ack", lang))
        handoff(uid, "quote_request", summary=aftercare.renew_brief(order), prio="P2")
    elif action == "later" and ui:
        await cleanup.collapse(bot, ui["id"], copy("aftercare.later_ack", lang))
