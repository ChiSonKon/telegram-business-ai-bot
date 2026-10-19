"""置顶(03 §8.3):订单卡进入待付款时置顶,交付 24 小时后/关闭后取消置顶;Business 默认关闭。"""
from __future__ import annotations

import logging

from ..config import cfg
from . import capabilities, tgx, ui_registry

logger = logging.getLogger(__name__)


def _now() -> float:
    from whitecat_core.runtime import now
    return now().timestamp()


def _emit(event: str, user_id: int, **props) -> None:
    try:
        from ..analytics.events import emit
        emit(event, user_id, **props)
    except Exception:
        pass


async def pin_card(bot, ui_id: int) -> bool:
    ui = ui_registry.get(ui_id)
    if not ui or ui["pinned"]:
        return False
    channel = "business" if ui["biz"] else "bot"
    if not cfg.get("experience.pins.enabled", default=True) or not capabilities.ok(channel, "pin"):
        return False
    if not await tgx.pin(bot, ui["chat_id"], ui["message_id"], ui["biz"]):
        capabilities.fail(channel, "pin")
        return False
    ui_registry.mark_pinned(ui_id, True, None)
    _emit("pin_set", ui["user_id"], kind=ui["kind"][:32])
    if channel == "bot" and capabilities.ok("bot", "pin_service_delete"):      # 置顶会产生一条服务消息;尽力删除(私聊中它属于机器人的 outgoing)
        if not await tgx.delete_messages(bot, ui["chat_id"], [ui["message_id"] + 1]):
            capabilities.fail("bot", "pin_service_delete")
    return True


def schedule_unpin(ui_id: int, after_s: float) -> None:
    ui_registry.mark_pinned(ui_id, True, _now() + after_s)


async def unpin_now(bot, ui_id: int) -> bool:
    ui = ui_registry.get(ui_id)
    if not ui or not ui["pinned"]:
        return False
    await tgx.unpin(bot, ui["chat_id"], ui["message_id"], ui["biz"])         # 消息已删除/权限变化时失败也视为已取消
    ui_registry.mark_pinned(ui_id, False, None)
    _emit("pin_cleared", ui["user_id"], kind=ui["kind"][:32])
    return True


async def unpin_job(bot) -> int:
    """定时任务:取消到期的置顶。返回处理条数。"""
    n = 0
    for ui in ui_registry.due_unpins(_now()):
        if await unpin_now(bot, ui["id"]):
            n += 1
    return n
