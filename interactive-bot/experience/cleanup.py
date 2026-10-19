"""会话整洁(03 §8.4):进度卡收起、作废卡去按钮、临时消息删除、旧欢迎卡更新提示。所有操作失败静默。"""
from __future__ import annotations

import logging

from ..config import copy
from ..ui import render
from ..ui.icons import to_plain_emoji
from . import tgx, ui_registry

logger = logging.getLogger(__name__)
EDIT_WINDOW_S = 48 * 3600


def _now() -> float:
    from whitecat_core.runtime import now
    return now().timestamp()


async def collapse(bot, ui_id: int, text: str) -> bool:
    """卡片收起为一行文字(无按钮)。"""
    ui = ui_registry.get(ui_id)
    if not ui:
        return False
    ok = await tgx.edit_text(bot, ui["chat_id"], ui["message_id"], render(text), ui["biz"], None)
    if not ok:
        ok = await tgx.edit_text(bot, ui["chat_id"], ui["message_id"], to_plain_emoji(render(text)), ui["biz"], None)
    if not ok:
        await tgx.strip_buttons(bot, ui["chat_id"], ui["message_id"], ui["biz"])
    ui_registry.set_state(ui_id, "collapsed")
    return ok


async def void(bot, ui_id: int, text: str) -> bool:
    """被替代/作废的卡片:改写为说明文字并去掉按钮。"""
    ui = ui_registry.get(ui_id)
    if not ui:
        return False
    ok = await tgx.edit_text(bot, ui["chat_id"], ui["message_id"], render(text), ui["biz"], None)
    if not ok:
        await tgx.strip_buttons(bot, ui["chat_id"], ui["message_id"], ui["biz"])
    ui_registry.set_state(ui_id, "void")
    return ok


async def delete_temp(bot, ui_id: int) -> bool:
    ui = ui_registry.get(ui_id)
    if not ui:
        return False
    ok = await tgx.delete_messages(bot, ui["chat_id"], [ui["message_id"]], ui["biz"])
    ui_registry.set_state(ui_id, "deleted")
    return ok


async def retire_previous(bot, user_id: int, kind: str, *, keep_id: int | None = None) -> int:
    """同类旧卡片去掉按钮(新卡发出后,旧卡上的按钮不应再可点)。"""
    n = 0
    for ui in ui_registry.live(user_id, kind):
        if ui["id"] == keep_id:
            continue
        await tgx.strip_buttons(bot, ui["chat_id"], ui["message_id"], ui["biz"])
        ui_registry.set_state(ui["id"], "void")
        n += 1
    return n


async def collapse_latest(bot, user_id: int, kind: str, text: str) -> bool:
    ui = ui_registry.latest(user_id, kind)
    return await collapse(bot, ui["id"], text) if ui else False


async def supersede_welcome(bot, user_id: int, *, keep_id: int | None = None) -> int:
    """同一会话里重复出现的旧欢迎卡:去按钮并追加"(已更新,请看下方)"。"""
    n = 0
    for ui in ui_registry.live(user_id, "welcome"):
        if ui["id"] == keep_id:
            continue
        await void(bot, ui["id"], "<i>" + copy("ui.updated_see_below", "zh") + " / " + copy("ui.updated_see_below", "en") + "</i>")
        n += 1
    return n
