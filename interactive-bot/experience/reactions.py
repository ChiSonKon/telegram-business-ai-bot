"""消息反应(03 §8.5,仅 Bot 私聊/群):只是"我收到了"的轻提示——点一下赞,约 1.5 秒后自动取消,不留痕迹。
付款截图 → 👌;同事处理中客户又发消息 → ✍;普通消息 → ❤️;话题里同事的消息 → ❤️。失败静默。"""
from __future__ import annotations

import asyncio

from ..config import cfg
from . import capabilities, tgx

HOLD_S = 1.5
_tasks: set = set()


def enabled(channel: str = "bot") -> bool:
    return channel == "bot" and bool(cfg.get("experience.reactions.enabled", default=True)) and capabilities.ok("bot", "react")


async def _clear_later(bot, chat_id: int, message_id: int, hold: float) -> None:
    await asyncio.sleep(hold)
    await tgx.clear_react(bot, chat_id, message_id)


async def flash(bot, chat_id: int, message_id: int | None, emoji: str, channel: str = "bot", hold: float = HOLD_S) -> bool:
    """点一个反应,hold 秒后自动取消(后台任务,不阻塞主流程)。"""
    if not message_id or not enabled(channel):
        return False
    ok = await tgx.react(bot, chat_id, message_id, emoji)
    if not ok:
        capabilities.fail("bot", "react")
        return False
    task = asyncio.create_task(_clear_later(bot, chat_id, message_id, hold))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


async def seen(bot, chat_id: int, message_id: int | None, channel: str = "bot") -> bool:
    """普通消息:❤️ 一下表示收到。"""
    return await flash(bot, chat_id, message_id, "❤️", channel)


async def receipt(bot, chat_id: int, message_id: int | None, channel: str = "bot") -> bool:
    """收到付款截图:👌。截图不是付款证明,这只是"我看到了"。"""
    if not message_id or not enabled(channel):
        return False
    return await flash(bot, chat_id, message_id, "👌", channel)


async def pending_ack(bot, chat_id: int, message_id: int | None, channel: str = "bot") -> bool:
    """客户的消息已转发给同事:✍。"""
    if not message_id or not enabled(channel):
        return False
    return await flash(bot, chat_id, message_id, "✍", channel)
