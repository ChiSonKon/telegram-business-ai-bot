"""
用户侧消息处理 — AI Pipeline 核心

处理流程:
1. 黑名单/限流检查
2. 检查 chat_mode（AI_MODE / HUMAN_MODE）
3. AI_MODE → RAG检索 → 加载Prompt → 短期记忆 → 长期记忆 → LLM推理 → 发送回复+按钮
4. HUMAN_MODE → 仅转发到Forum Topic，不调用LLM
"""
import asyncio
import os
import random
import re
import time
from string import ascii_letters as letters

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import ContextTypes, ConversationHandler
from telegram.helpers import mention_html

from db.database import SessionMaker
from db.model import FormnStatus, MediaGroupMesssage, MessageMap, User

from .. import (
    admin_user_ids,
    bot_token,
    logger,
    welcome_message,
    blacklist_user_ids,
)
from ..global_settings import get_global_setting, get_admin_group_id
from ..timeout_tracker import track_activity, dismiss_care
from ..utils import delete_message_later
from ..llm.fallback_client import FallbackLLMClient
from ..llm.prompt_loader import load_system_prompt
from ..rag.retriever import search_knowledge
from ..memory.short_term import ShortTermMemory
from ..memory.long_term import LongTermMemory
from ..middleware.state_machine import ChatMode, get_chat_mode, set_chat_mode, is_ai_mode
from ..start_settings import get_bottom_button_action, build_call_human_markup
from ..middleware.rate_limiter import RateLimiter
from ..humanlike_sender import send_humanlike_messages
from ..debouncer import debounce_user_message
from whitecat_core import runtime
from ..ai_turn import run_ai_turn, run_first_turn, shared
from ..frontdesk import abuse
from ..frontdesk.gate import admit
from .callback_handlers import challenge_if_abusive
from ..frontdesk.service import handoff, is_human_locked, record_message
from .business_handlers import VOICE_REQUEST_KEYWORDS
from ..ui import btn, kb, plain_markup, render, send_card
from ..ui.cards import (
    AI_OFFLINE_TEXT,
    CARE_TEXT,
    HUMAN_MODE_TEXT,
    admin_control_markup,
    care_markup,
    human_mode_markup,
    human_request_text,
)
from ..commerce.notify import register_receipt

# ═══ 全局单例 ═══
llm_client, short_memory, long_memory = shared()
rate_limiter = RateLimiter(default_max=10)

db = SessionMaker()


def update_user_db(user: telegram.User):
    """更新用户数据库记录"""
    existing = db.query(User).filter(User.user_id == user.id).first()
    if existing:
        return
    u = User(
        user_id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        chat_mode="AI_MODE",
    )
    db.add(u)
    db.commit()


async def send_contact_card(
    chat_id, message_thread_id, user: User, update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """发送用户名片到管理群"""
    buttons = [[
        btn("高级会员", icon="crown", style="success") if user.is_premium
        else btn("普通会员", icon="user")
    ]]
    if user.username:
        buttons.append([btn("直接联络", icon="chat", style="primary", url=f"https://t.me/{user.username}")])

    # 添加管理侧控制按钮
    buttons += admin_control_markup(user.user_id).inline_keyboard

    card = (
        f"[[user]] {mention_html(user.user_id, user.first_name)}\n"
        f"[[tag]] <code>{user.user_id}</code>\n"
        f"[[link]] @{user.username if user.username else '无'}"
    )
    user_photo = await context.bot.get_user_profile_photos(user.user_id)

    if user_photo.total_count:
        pic = user_photo.photos[0][-1].file_id
        photo_kwargs = dict(chat_id=chat_id, photo=pic, message_thread_id=message_thread_id, parse_mode="HTML")
        try:
            await context.bot.send_photo(caption=render(card), reply_markup=kb(buttons), **photo_kwargs)
        except BadRequest:
            await context.bot.send_photo(caption=render(card, rich=False), reply_markup=plain_markup(kb(buttons)), **photo_kwargs)
    else:
        await send_card(context.bot, chat_id, card, kb(buttons), message_thread_id=message_thread_id)


# Telegram 官方支持的 6 种话题色彩 (RGB 整数)
FORUM_TOPIC_COLORS = [
    7322096,   # 0x6FB9F0 蓝色
    16766590,  # 0xFFD67E 黄色
    13338331,  # 0xCB86DB 紫色
    9367192,   # 0x8EEE98 绿色
    16749490,  # 0xFF93B2 粉色
    16478047,  # 0xFB6F5F 红色
]

_topic_lock = asyncio.Lock()


async def _ensure_forum_topic(user: telegram.User, u: User | None, context: ContextTypes.DEFAULT_TYPE) -> int:
    """确保用户拥有唯一的 Forum Topic，支持随机色彩并防止并发竞争创建重复话题"""
    async with _topic_lock:
        with SessionMaker() as sdb:
            db_user = sdb.query(User).filter(User.user_id == user.id).first()
            if db_user and db_user.message_thread_id:
                if u:
                    u.message_thread_id = db_user.message_thread_id
                return db_user.message_thread_id

            chat_id = get_admin_group_id()
            try:
                # 随机选择官方色彩;网络超时/抖动最多重试 3 次(一次超时曾导致该客户永远没有话题)
                chosen_color = random.choice(FORUM_TOPIC_COLORS)
                formn = None
                for attempt in range(3):
                    try:
                        formn = await context.bot.create_forum_topic(
                            chat_id,
                            name=f"{user.full_name}|{user.id}"[:128],
                            icon_color=chosen_color,
                            read_timeout=20, write_timeout=20, connect_timeout=10,
                        )
                        break
                    except (TimedOut, NetworkError) as exc:
                        logger.warning(f"创建 Forum Topic 超时/网络错误,第 {attempt + 1} 次 (chat_id={chat_id}): {exc}")
                        if attempt == 2:
                            raise
                        await asyncio.sleep(1.5 * (attempt + 1))
                message_thread_id = formn.message_thread_id

                if not db_user:
                    db_user = User(
                        user_id=user.id,
                        first_name=getattr(user, "first_name", None),
                        last_name=getattr(user, "last_name", None),
                        username=getattr(user, "username", None),
                        chat_mode="AI_MODE",
                        message_thread_id=message_thread_id,
                    )
                    sdb.add(db_user)
                else:
                    db_user.message_thread_id = message_thread_id

                sdb.commit()

                if u:
                    u.message_thread_id = message_thread_id

                try:                                       # 话题已建好并落库:欢迎/名片发送失败不应让调用方误以为没有话题
                    await context.bot.send_message(
                        chat_id,
                        f"新的用户 {mention_html(user.id, user.full_name)} 开始了一个新的会话。",
                        message_thread_id=message_thread_id,
                        parse_mode="HTML",
                    )
                    await send_contact_card(chat_id, message_thread_id, db_user, None, context)
                except Exception as exc:
                    logger.warning(f"话题名片发送失败 (thread={message_thread_id}): {type(exc).__name__}")
                return message_thread_id

            except Exception as e:
                logger.warning(f"创建 Forum Topic 失败 (chat_id={chat_id}): {e}")
                return 0


async def ensure_topic_for_customer(tg_user, context: ContextTypes.DEFAULT_TYPE) -> int:
    """客户一进来(/start、点按钮)就在管理群开话题,不必等到发第一句话。管理员本人不开;失败只记日志,下次互动会重试。"""
    if not tg_user or tg_user.id in admin_user_ids:
        return 0
    try:
        with SessionMaker() as sdb:
            row = sdb.query(User).filter(User.user_id == tg_user.id).first()
            if row and row.message_thread_id:
                return row.message_thread_id
        return await _ensure_forum_topic(tg_user, None, context)
    except Exception as exc:
        logger.warning(f"ensure_topic_for_customer failed: {type(exc).__name__}")
        return 0


async def _forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, message_thread_id: int, masked_text: str | None = None):
    """转发消息到管理群 Forum Topic"""
    chat_id = get_admin_group_id()
    if not message_thread_id:
        return
    params = {"message_thread_id": message_thread_id}

    if update.message.reply_to_message:
        reply_in_user_chat = update.message.reply_to_message.message_id
        msg_map = db.query(MessageMap).filter(
            MessageMap.user_chat_message_id == reply_in_user_chat
        ).first()
        if msg_map:
            params["reply_to_message_id"] = msg_map.group_chat_message_id

    try:
        chat = await context.bot.get_chat(chat_id)
        if masked_text is not None:                       # 含完整卡密:不复制原消息,改发打码后的文本
            sent_msg = await context.bot.send_message(chat_id=chat_id, text="🔒 " + masked_text, **params)
        else:
            sent_msg = await chat.send_copy(
                update.effective_chat.id, update.message.id, **params
            )
        msg_map = MessageMap(
            user_chat_message_id=update.message.id,
            group_chat_message_id=sent_msg.message_id,
            user_id=update.effective_user.id,
        )
        db.add(msg_map)
        db.commit()
    except Exception as e:
        logger.error(f"转发到管理群失败: {e}")


async def _handle_human_bottom_button(update: Update, context: ContextTypes.DEFAULT_TYPE, message_thread_id: int):
    """处理底部按钮中的转人工动作。"""
    user = update.effective_user
    set_chat_mode(user.id, ChatMode.HUMAN_MODE)
    dismiss_care(user.id)

    await send_card(context.bot, update.effective_chat.id, HUMAN_MODE_TEXT, human_mode_markup(user.id))

    try:
        if message_thread_id:
            await send_card(
                context.bot,
                get_admin_group_id(),
                human_request_text(user.id, user.full_name),
                admin_control_markup(user.id),
                message_thread_id=message_thread_id,
            )
    except Exception as e:
        logger.error(f"底部按钮转人工通知管理群失败: {e}")


def _needs_pick(user_id: int) -> bool:
    """还没点过任何业务按钮、也没有历史对话/订单的客户。"""
    from db.model import FrontdeskCase, FrontdeskMessage, Order
    from db.database import SessionMaker
    with SessionMaker() as s:
        c = s.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=0).order_by(FrontdeskCase.id.desc()).first()
        if c and c.state not in (None, "FRONTDESK"):
            return False
        if s.query(FrontdeskMessage.id).filter(FrontdeskMessage.user_id == user_id, FrontdeskMessage.direction == "in").first():
            return False
        if s.query(Order.id).filter(Order.user_id == user_id).first():
            return False
    return True


async def _require_pick(update, context, user) -> bool:
    """True = 已拦截并回了菜单卡。管理员不受限;同一客户 20 秒内只回一次卡,避免刷屏。"""
    from ..admin.permissions import is_admin
    if is_admin(user.id) or not _needs_pick(user.id):
        return False
    now = time.time()
    if now - context.user_data.get("pick_card_at", 0) > 20:
        context.user_data["pick_card_at"] = now
        from ..frontdesk import router
        lang = runtime.lang_for(user.id, update.message.text or update.message.caption or None)
        await router._send_welcome(context.bot, user.id, update.effective_chat.id, lang, None, "bot")
    return True


async def forwarding_message_u2a(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    客户侧核心消息处理器。
    AI_MODE:    走完整 AI Pipeline
    HUMAN_MODE: 仅转发到后台
    """
    # 安全检查
    if not update.message:
        return

    user = update.effective_user

    # 黑名单检查
    if user.id in blacklist_user_ids:
        return  # 静默丢弃

    # 限流检查
    if not rate_limiter.is_allowed(user.id):
        abuse.strike(user.id, "rate_limit")
        await update.message.reply_html("⏳ 请稍后再发送消息，你的请求太频繁了。")
        return

    # 消息间隔检查
    interval = get_global_setting("message_interval")
    if interval:
        if context.user_data.get("last_message_time", 0) > time.time() - interval:
            await update.message.reply_html("请不要频繁发送消息。")
            return
        context.user_data["last_message_time"] = time.time()

    update_user_db(user)

    u = db.query(User).filter(User.user_id == user.id).first()

    # 检查 Forum Topic 是否已关闭
    message_thread_id = u.message_thread_id
    if message_thread_id:
        f = db.query(FormnStatus).filter(
            FormnStatus.message_thread_id == message_thread_id
        ).first()
        if f and f.status == "closed":
            await update.message.reply_html(
                "客服已经关闭对话。如需联系，请利用其他途径联络客服回复和你的对话。"
            )
            return

    # 确保有 Forum Topic
    message_thread_id = await _ensure_forum_topic(user, u, context)

    # ═══ 1.5 先选业务:Bot 私聊里还没选过业务的新用户,自由发言一律不转发、不进 AI,先回菜单卡(防广告骚扰)═══
    if await _require_pick(update, context, user):
        return

    # ═══ 2. 处理媒体组（保留原有逻辑） ═══
    if update.message.media_group_id:
        from .admin_handlers import send_media_group_later
        msg = MediaGroupMesssage(
            chat_id=update.message.chat.id,
            message_id=update.message.message_id,
            media_group_id=update.message.media_group_id,
            is_header=False,
            caption_html=update.message.caption_html,
        )
        db.add(msg)
        db.commit()
        if update.message.media_group_id != context.user_data.get("current_media_group_id", 0):
            context.user_data["current_media_group_id"] = update.message.media_group_id
            await send_media_group_later(
                5, user.id, get_admin_group_id(), update.message.media_group_id, "u2a", context
            )
            if not is_human_locked(user.id):
                await register_receipt(context.bot, user.id)
        return

    user_text = update.message.text or update.message.caption or ""
    bottom_action = get_bottom_button_action(user_text) if update.message.text else None

    # ═══ 3. 转发原始消息到管理群 ═══(完整卡密:不回显给任何人,管理群只见打码文本)
    from ..frontdesk import aftercare
    masked_text = aftercare.mask_keys(user_text) if user_text else None
    leaked = bool(masked_text) and masked_text != user_text
    await _forward_to_admin(update, context, message_thread_id, masked_text if leaked else None)
    if leaked:
        user_text = masked_text
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
        except Exception:
            pass
        from ..config import copy as _copy
        await send_card(context.bot, update.effective_chat.id, _copy("support.license.full_key_warning", runtime.lang_for(user.id)), None)

    if bottom_action and bottom_action.get("action") == "human":
        await _handle_human_bottom_button(update, context, message_thread_id)
        return

    if bottom_action and bottom_action.get("action") == "text" and bottom_action.get("value"):
        user_text = bottom_action["value"]

    # 客户发送贴纸时，若处于 AI 模式，主理人自然回发一个互动贴纸
    if getattr(update.message, "sticker", None) and is_ai_mode(user.id):
        from ..sticker_sender import send_humanlike_sticker
        await send_humanlike_sticker(context.bot, update.effective_chat.id, "greeting")
        track_activity(user.id, update.effective_chat.id, None, is_ai_reply=True)
        return

    if (getattr(update.message, "voice", None) or getattr(update.message, "video_note", None)) and not user_text.strip():
        if not is_human_locked(user.id):
            from ..config import copy
            await send_card(context.bot, update.effective_chat.id, copy("voice.reply", runtime.lang_for(user.id)), None)
        return

    if not user_text.strip():
        # 非文本消息（图片/文件等），仅转发不触发 AI
        if not is_human_locked(user.id):
            from ..frontdesk.router import note_image_in_intake
            if not await note_image_in_intake(context.bot, user.id, update.effective_chat.id):
                await register_receipt(context.bot, user.id)
                from ..experience import reactions
                await reactions.receipt(context.bot, update.effective_chat.id, update.message.message_id)
        else:
            from ..frontdesk.router import notify_pending
            if not await notify_pending(context.bot, user.id, update.effective_chat.id):
                from ..experience import reactions
                await reactions.pending_ack(context.bot, update.effective_chat.id, update.message.message_id)
        return

    # 入口护盾:洪泛/重复折叠(不耗 token);频繁触发才计入验证判定
    shield = runtime.shield.check(user.id, user_text)
    if not shield.allow:
        logger.info("shield dropped msg from %s: %s", user.id, shield.reason)
        abuse.strike(user.id, shield.reason)
        return
    user_text = shield.text
    if not is_human_locked(user.id):                         # 人工处理中的已由 ✍ 提示;其余普通消息 ❤️ 一下表示收到
        from ..experience import reactions as _rx
        await _rx.seen(context.bot, update.effective_chat.id, update.message.message_id)
    lang = runtime.lang_for(user.id, user_text)
    record_message(user.id, "in", user_text, channel="bot")

    # 交易哈希:正在等待哈希或直接粘贴了 64 位哈希且有待付订单 → 付款核对,不进入 AI
    from .gateway_handlers import maybe_txid
    if await maybe_txid(update, context, user_text):
        return

    # 人工已锁定:消息已转发管理群,AI 不介入;待跟进期间回"处理中"卡(30 分钟节流)
    if is_human_locked(user.id) or not is_ai_mode(user.id):
        if is_human_locked(user.id):
            short_memory.add(user.id, "user", user_text)         # 同事处理期间客户说的话也进入记忆,AI 恢复后不失忆
            from ..frontdesk.router import notify_pending
            if not await notify_pending(context.bot, user.id, update.effective_chat.id):
                from ..experience import reactions
                await reactions.pending_ack(context.bot, update.effective_chat.id, update.message.message_id)
        return

    # 前台:先预检(规则驱动、零模型),收齐后才交给 AI
    gate = await admit(context.bot, user.id, update.effective_chat.id, user_text, channel="bot")
    if gate.care:
        dismiss_care(user.id)
        await send_card(context.bot, update.effective_chat.id, CARE_TEXT, care_markup(user.id))
        return
    if not gate.ai:
        return

    # 仅在频繁超额调用 AI 时才要求人机验证(预检阶段永远没有)
    if await challenge_if_abusive(context.bot, update.effective_chat.id, user, context, lang):
        return

    if gate.immediate:
        await run_first_turn(context.bot, user.id, update.effective_chat.id, gate.texts)
        return

    track_activity(user.id, update.effective_chat.id, None, is_ai_reply=False)
    wants_voice = any(k in user_text for k in VOICE_REQUEST_KEYWORDS)
    ticket = runtime.epochs.ticket(runtime.epoch_key(user.id))

    async def _mirror(reply, sent_msgs):
        if not message_thread_id:
            return
        ai_admin_msg = await context.bot.send_message(
            get_admin_group_id(), f"🤖 AI 回复:\n\n{reply}", message_thread_id=message_thread_id,
        )
        last_sent_id = sent_msgs[-1].message_id if sent_msgs else update.message.message_id
        db.add(MessageMap(user_chat_message_id=last_sent_id, group_chat_message_id=ai_admin_msg.message_id, user_id=user.id))
        db.commit()

    async def _do_user_ai(texts: list[str]):
        await run_ai_turn(
            bot=context.bot, user_id=user.id, chat_id=update.effective_chat.id, texts=texts, llm=llm_client,
            short_memory=short_memory, long_memory=long_memory, ticket=ticket,
            wants_voice=wants_voice, mirror=_mirror,
        )

    await debounce_user_message(user.id, user_text, _do_user_ai)
