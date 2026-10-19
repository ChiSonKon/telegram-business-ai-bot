"""
TG Business 消息处理 — 监听 business_message 事件

当 Bot 被绑定到 Telegram Business 账号后，
通过该账号的私聊消息会通过 business_message 类型传入。
"""
import json
import re
from typing import Any, Dict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from telegram.helpers import mention_html

from db.database import SessionMaker
from db.model import User, FrontdeskMessage

from .. import (
    admin_user_ids,
    blacklist_user_ids,
    logger,
    rate_limit_per_minute,
)
from ..global_settings import get_admin_group_id
from ..llm.fallback_client import FallbackLLMClient
from ..llm.prompt_loader import load_system_prompt
from ..rag.retriever import search_knowledge
from ..memory.short_term import ShortTermMemory
from ..memory.long_term import LongTermMemory
from ..middleware.state_machine import is_ai_mode, ChatMode, set_chat_mode
from ..middleware.rate_limiter import RateLimiter
from ..humanlike_sender import send_humanlike_messages
from ..debouncer import debounce_user_message
from ..commerce.notify import register_receipt
from .broadcast_handlers import upsert_business_contact
from whitecat_core import runtime
from ..ai_turn import run_ai_turn, run_first_turn, shared
from ..frontdesk import abuse
from ..frontdesk.gate import admit
from .callback_handlers import challenge_if_abusive
from ..frontdesk.service import handoff, is_human_locked, record_message
from ..timeout_tracker import track_activity, dismiss_care
from ..ui import send_card
from ..ui.cards import CARE_TEXT, care_markup

# 单例
_llm_client, _short_memory, _long_memory = shared()
_rate_limiter = RateLimiter(default_max=rate_limit_per_minute)

db = SessionMaker()


def _clean_markdown(text: str) -> str:
    """清理 Markdown 格式（TG 纯文本展示）"""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'```[\s\S]*?```', lambda m: m.group().replace('```', ''), text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    return text


async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理 Telegram Business 接管的消息。
    """
    message = update.business_message or update.message
    if not message:
        return

    user = message.from_user
    if not user or user.is_bot:
        return

    # 风控：跳过管理员的私人聊天
    from ..admin import sandbox
    if user.id in admin_user_ids and not sandbox.is_client_view(user.id):
        return

    # 黑名单
    if user.id in blacklist_user_ids:
        return

    # 限流
    if not _rate_limiter.is_allowed(user.id):
        abuse.strike(user.id, "rate_limit")
        return

    user_text = (message.text or message.caption or "").strip()
    biz_conn_id = message.business_connection_id
    upsert_business_contact(message)

    # 记录用户活跃状态（非 AI 回复）
    track_activity(user.id, message.chat.id, biz_conn_id, is_ai_reply=False)

    # 确保用户记录存在与 Forum Topic 绑定
    thread_id = None
    with SessionMaker() as user_db:
        existing = user_db.query(User).filter(User.user_id == user.id).first()
        if not existing:
            existing = User(
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                username=user.username,
                chat_mode="AI_MODE",
            )
            user_db.add(existing)
            user_db.commit()
            user_db.refresh(existing)

        if not existing.message_thread_id:
            try:
                from .user_handlers import _ensure_forum_topic
                thread_id = await _ensure_forum_topic(user, existing, context)
            except Exception as e:
                logger.warning(f"确保 Business 客户 Forum Topic 失败: {e}")
        else:
            thread_id = existing.message_thread_id

    # 转发客户输入到管理群 Forum Topic
    current_admin_group = get_admin_group_id()
    if thread_id and user_text:
        try:
            await context.bot.send_message(
                chat_id=current_admin_group,
                text=f"💬 客户 (@{user.username or user.id}):\n{user_text}",
                message_thread_id=thread_id,
            )
        except Exception:
            pass

    # ═══ 1. 入口护盾:规范化、洪泛/重复折叠(不耗 token、不回复)═══
    if user_text:
        shield = runtime.shield.check(user.id, user_text)
        if not shield.allow:
            logger.info("shield dropped business msg from %s: %s", user.id, shield.reason)
            abuse.strike(user.id, shield.reason)
            return
        user_text = shield.text

    # 客户发送贴纸时，若处于 AI 模式，自然回发一个互动贴纸
    if getattr(message, "sticker", None) and is_ai_mode(user.id) and not is_human_locked(user.id):
        from ..sticker_sender import send_humanlike_sticker
        await send_humanlike_sticker(context.bot, message.chat.id, "greeting", biz_conn_id)
        return

    # 语音/圆形视频:暂不支持收听,礼貌请客户打字(人工锁中不打扰)
    if (getattr(message, "voice", None) or getattr(message, "video_note", None)) and not user_text:
        if not is_human_locked(user.id):
            from ..config import copy
            from ..frontdesk.router import send as fd_send
            await fd_send(context.bot, user.id, message.chat.id, copy("voice.reply", runtime.lang_for(user.id)), None, biz_conn_id, runtime.lang_for(user.id))
        return

    # 付款截图/图片:只进入"待核验"并通知管理员,绝不视为付款成功
    if not user_text:
        if not is_human_locked(user.id):
            from ..frontdesk.router import note_image_in_intake
            if not await note_image_in_intake(context.bot, user.id, message.chat.id, biz_conn_id):
                await register_receipt(context.bot, user.id)
        return

    record_message(user.id, "in", user_text, channel="business")
    lang = runtime.lang_for(user.id, user_text)

    # ═══ 2. 人工已锁定:消息已转发给管理群,AI 不介入;待跟进期间回"处理中"卡 ═══
    if is_human_locked(user.id) or not is_ai_mode(user.id):
        if is_human_locked(user.id):
            _short_memory.add(user.id, "user", user_text)
            from ..frontdesk.router import notify_pending
            await notify_pending(context.bot, user.id, message.chat.id, biz_conn_id)
        return

    # ═══ 3. 前台:先预检(规则驱动、零模型),收齐后才交给 AI ═══
    gate = await admit(context.bot, user.id, message.chat.id, user_text, channel="business", biz_conn_id=biz_conn_id)
    if gate.care:
        dismiss_care(user.id)
        await send_card(context.bot, message.chat.id, CARE_TEXT, care_markup(user.id), biz_conn_id)
        return
    if not gate.ai:
        return

    # 仅在频繁超额调用 AI 时才要求人机验证
    if await challenge_if_abusive(context.bot, message.chat.id, user, context, lang, biz_conn_id):
        return

    if gate.immediate:
        await run_first_turn(context.bot, user.id, message.chat.id, gate.texts, biz_conn_id)
        return

    # ═══ 4. AI 回合(防抖合并 → 统一网关)═══
    logger.info("Business msg from %s accepted (len=%d)", user.id, len(user_text))
    wants_voice = any(k in user_text for k in VOICE_REQUEST_KEYWORDS)
    ticket = runtime.epochs.ticket(runtime.epoch_key(user.id))

    async def _mirror(reply, _sent):
        if thread_id:
            await context.bot.send_message(current_admin_group, f"🤖 AI 回复客户:\n\n{reply}", message_thread_id=thread_id)

    async def _do_business_ai(texts: list[str]):
        await run_ai_turn(
            bot=context.bot, user_id=user.id, chat_id=message.chat.id, texts=texts, llm=_llm_client,
            short_memory=_short_memory, long_memory=_long_memory, ticket=ticket,
            biz_conn_id=biz_conn_id, wants_voice=wants_voice, mirror=_mirror,
        )

    await debounce_user_message(user.id, user_text, _do_business_ai)


VOICE_REQUEST_KEYWORDS = [
    "发语音", "发条语音", "能语音吗", "能发语音吗", "听听声音", "发段语音", "说句话", "语音聊", "发一条语音", "说两句", "来句语音",
    "voice message", "send a voice", "voice note",
]


async def handle_edited_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edited Business messages replace pending text or are mirrored without a second AI turn."""
    message = getattr(update, "edited_business_message", None) or getattr(update, "edited_message", None)
    if not message or not message.from_user or message.from_user.is_bot:
        return
    text = (message.text or message.caption or "").strip()
    if not text:
        return
    uid, mid = message.from_user.id, message.message_id
    with SessionMaker() as db:
        row = db.query(FrontdeskMessage).filter_by(user_id=uid, message_id=mid, direction="in").first()
        if row:
            row.text = text[:12000]
            db.commit()
    if row:
        with SessionMaker() as db:
            user = db.query(User).filter(User.user_id == uid).first()
        if user and user.message_thread_id:
            try:
                await context.bot.send_message(get_admin_group_id(), f"✏️ 客户编辑了消息 (@{user.username or uid}):\n{text}", message_thread_id=user.message_thread_id)
            except Exception:
                pass
        return
    await handle_business_message(update, context)
