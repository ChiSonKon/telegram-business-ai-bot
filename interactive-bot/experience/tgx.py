"""Bot API 7.4 之后的能力封装。PTB 21.3 不认识的方法/字段一律经 api_kwargs 或 do_api_request 调用;统一吞掉异常返回 bool。"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _biz(biz: str | None) -> dict[str, Any]:
    return {"api_kwargs": {"business_connection_id": biz}} if biz else {}


async def pin(bot, chat_id: int, message_id: int, biz: str | None = None) -> bool:
    try:
        await bot.pin_chat_message(chat_id=chat_id, message_id=message_id, disable_notification=True, **_biz(biz))
        return True
    except Exception as exc:
        logger.info("pin failed: %s", type(exc).__name__)
        return False


async def unpin(bot, chat_id: int, message_id: int, biz: str | None = None) -> bool:
    try:
        await bot.unpin_chat_message(chat_id=chat_id, message_id=message_id, **_biz(biz))
        return True
    except Exception as exc:
        logger.info("unpin failed: %s", type(exc).__name__)
        return False


async def react(bot, chat_id: int, message_id: int, emoji: str) -> bool:
    """设置一个标准表情反应(非付费)。仅 Bot 私聊/群可用,Business 无此参数。"""
    try:
        from telegram import ReactionTypeEmoji
        await bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=[ReactionTypeEmoji(emoji=emoji)])
        return True
    except Exception as exc:
        logger.info("reaction failed: %s", type(exc).__name__)
        return False


async def clear_react(bot, chat_id: int, message_id: int) -> bool:
    """取消反应(清空)。"""
    try:
        await bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=[])
        return True
    except Exception as exc:
        logger.info("clear reaction failed: %s", type(exc).__name__)
        return False


async def delete_messages(bot, chat_id: int, message_ids: list[int], biz: str | None = None) -> bool:
    try:
        if biz:
            await bot.do_api_request("deleteBusinessMessages", api_kwargs={"business_connection_id": biz, "message_ids": message_ids})
        else:
            for mid in message_ids:
                await bot.delete_message(chat_id=chat_id, message_id=mid)
        return True
    except Exception as exc:
        logger.info("delete failed: %s", type(exc).__name__)
        return False


async def send_draft(bot, chat_id: int, draft_id: int, text: str = "", can_stop: bool = True) -> bool:
    """sendMessageDraft:空文本 = 客户端显示 Thinking… 占位;草稿是 30 秒临时预览,需定期刷新。"""
    try:
        await bot.do_api_request("sendMessageDraft", api_kwargs={"chat_id": chat_id, "draft_id": draft_id, "text": text, "can_stop": can_stop})
        return True
    except Exception as exc:
        logger.info("sendMessageDraft failed: %s", type(exc).__name__)
        return False


async def get_business_connection(bot, biz: str) -> dict | None:
    try:
        res = await bot.do_api_request("getBusinessConnection", api_kwargs={"business_connection_id": biz})
        return res if isinstance(res, dict) else getattr(res, "__dict__", None)
    except Exception as exc:
        logger.info("getBusinessConnection failed: %s", type(exc).__name__)
        return None


async def edit_text(bot, chat_id: int, message_id: int, text: str, biz: str | None = None, markup=None) -> bool:
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML", reply_markup=markup, **_biz(biz))
        return True
    except Exception as exc:
        if "not modified" in str(exc).lower():
            return True
        logger.info("edit failed: %s", type(exc).__name__)
        return False


async def strip_buttons(bot, chat_id: int, message_id: int, biz: str | None = None) -> bool:
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None, **_biz(biz))
        return True
    except Exception as exc:
        logger.info("strip buttons failed: %s", type(exc).__name__)
        return False
