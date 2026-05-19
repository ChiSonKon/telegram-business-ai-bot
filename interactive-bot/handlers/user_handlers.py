"""
用户侧消息处理 — AI Pipeline 核心

处理流程:
1. 黑名单/限流检查
2. 检查 chat_mode（AI_MODE / HUMAN_MODE）
3. AI_MODE → RAG检索 → 加载Prompt → 短期记忆 → 长期记忆 → LLM推理 → 发送回复+按钮
4. HUMAN_MODE → 仅转发到Forum Topic，不调用LLM
"""
import os
import random
import re
import time
from string import ascii_letters as letters

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler
from telegram.helpers import mention_html

from db.database import SessionMaker
from db.model import FormnStatus, MediaGroupMesssage, MessageMap, User

from .. import (
    admin_group_id,
    admin_user_ids,
    bot_token,
    logger,
    welcome_message,
    blacklist_user_ids,
)
from ..global_settings import get_global_setting
from ..utils import delete_message_later
from ..llm.fallback_client import FallbackLLMClient
from ..llm.prompt_loader import load_system_prompt
from ..rag.retriever import search_knowledge
from ..memory.short_term import ShortTermMemory
from ..memory.long_term import LongTermMemory
from ..middleware.state_machine import ChatMode, get_chat_mode, set_chat_mode, is_ai_mode
from ..start_settings import get_bottom_button_action, build_call_human_markup
from ..middleware.rate_limiter import RateLimiter
from .payment_rules import (
    build_paid_text_message,
    build_payment_media_message,
    build_payment_message,
    update_payment_context,
)

# ═══ 全局单例 ═══
llm_client = FallbackLLMClient()
short_memory = ShortTermMemory(max_messages=15)
long_memory = LongTermMemory()
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
    buttons = []
    buttons.append(
        [
            InlineKeyboardButton(
                f"{'🏆 高级会员' if user.is_premium else '✈️ 普通会员'}",
                callback_data="ignore",
            )
        ]
    )
    if user.username:
        buttons.append(
            [InlineKeyboardButton("👤 直接联络", url=f"https://t.me/{user.username}")]
        )

    # 添加管理侧控制按钮
    buttons.append([
        InlineKeyboardButton("🔕 接管对话", callback_data=f"admin_takeover_{user.user_id}"),
        InlineKeyboardButton("🤖 恢复 AI", callback_data=f"admin_restore_{user.user_id}"),
    ])

    user_photo = await context.bot.get_user_profile_photos(user.user_id)

    if user_photo.total_count:
        pic = user_photo.photos[0][-1].file_id
        await context.bot.send_photo(
            chat_id,
            photo=pic,
            caption=f"👤 {mention_html(user.user_id, user.first_name)}\n\n📱 {user.user_id}\n\n🔗 @{user.username if user.username else '无'}",
            message_thread_id=message_thread_id,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML",
        )
    else:
        await context.bot.send_message(
            chat_id,
            text=f"👤 {mention_html(user.user_id, user.first_name)}\n📱 {user.user_id}\n🔗 @{user.username if user.username else '无'}",
            message_thread_id=message_thread_id,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML",
        )


async def _ensure_forum_topic(user: telegram.User, u: User, context: ContextTypes.DEFAULT_TYPE) -> int:
    """确保用户有一个 Forum Topic，没有则创建"""
    chat_id = admin_group_id
    message_thread_id = u.message_thread_id

    if not message_thread_id:
        formn = await context.bot.create_forum_topic(
            chat_id,
            name=f"{user.full_name}|{user.id}",
        )
        message_thread_id = formn.message_thread_id
        u.message_thread_id = message_thread_id
        await context.bot.send_message(
            chat_id,
            f"新的用户 {mention_html(user.id, user.full_name)} 开始了一个新的会话。",
            message_thread_id=message_thread_id,
            parse_mode="HTML",
        )
        await send_contact_card(chat_id, message_thread_id, u, None, context)
        db.add(u)
        db.commit()

    return message_thread_id


async def _forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, message_thread_id: int):
    """转发消息到管理群 Forum Topic"""
    chat_id = admin_group_id
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

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🤖 恢复AI对话",
            callback_data=f"restore_ai_{user.id}",
        )]
    ])

    await update.message.reply_text(
        "已呼叫老板（白猫），请稍候...\n\n"
        "老板看到后会第一时间回复你。\n"
        "在此期间你可以继续发送消息，我会帮你转达。",
        reply_markup=keyboard,
    )

    try:
        keyboard_admin = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔕 接管对话", callback_data=f"admin_takeover_{user.id}"),
                InlineKeyboardButton("🤖 恢复 AI", callback_data=f"admin_restore_{user.id}"),
            ]
        ])
        await context.bot.send_message(
            admin_group_id,
            (
                "🚨🚨🚨 客户通过底部按钮呼叫人工 🚨🚨🚨\n\n"
                f"👤 {mention_html(user.id, user.full_name)}\n"
                f"📱 {user.id}\n\n"
                "请尽快回复！"
            ),
            message_thread_id=message_thread_id,
            reply_markup=keyboard_admin,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"底部按钮转人工通知管理群失败: {e}")


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

    # 管理员跳过验证码
    if not get_global_setting("disable_captcha") and user.id not in admin_user_ids:
        from .callback_handlers import check_human
        if not await check_human(update, context):
            return

    # 黑名单检查
    if user.id in blacklist_user_ids:
        return  # 静默丢弃

    # 限流检查
    if not rate_limiter.is_allowed(user.id):
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
                5, user.id, admin_group_id, update.message.media_group_id, "u2a", context
            )
            payment_media_reply = build_payment_media_message(context.user_data)
            if payment_media_reply and is_ai_mode(user.id):
                await update.message.reply_text(payment_media_reply)
        return

    user_text = update.message.text or update.message.caption or ""
    bottom_action = get_bottom_button_action(user_text) if update.message.text else None

    # ═══ 3. 转发原始消息到管理群 ═══
    await _forward_to_admin(update, context, message_thread_id)

    if bottom_action and bottom_action.get("action") == "human":
        await _handle_human_bottom_button(update, context, message_thread_id)
        return

    if bottom_action and bottom_action.get("action") == "text" and bottom_action.get("value"):
        user_text = bottom_action["value"]

    # ═══ 4. AI / 人工 分流 ═══

    if not user_text.strip():
        # 非文本消息（图片/文件等），仅转发不触发 AI
        payment_media_reply = build_payment_media_message(context.user_data)
        if payment_media_reply and is_ai_mode(user.id):
            await update.message.reply_text(payment_media_reply)
        return

    if is_ai_mode(user.id):
        # ═══ AI Pipeline ═══
        try:
            # 4a. RAG 检索
            rag_context = search_knowledge(user_text)

            # 4b. 加载动态 System Prompt
            system_prompt = load_system_prompt()

            # 4c. 短期记忆
            short_memory.add(user.id, "user", user_text)
            messages = short_memory.get_history(user.id)

            # 4d. 长期记忆召回
            long_term_ctx = long_memory.get_user_summary(user.id, user_text)

            # 4e. LLM 推理
            ai_reply = await llm_client.chat(
                system_prompt=system_prompt,
                messages=messages,
                rag_context=rag_context,
                long_term_context=long_term_ctx,
            )

            # 4f. 存储到记忆
            short_memory.add(user.id, "assistant", ai_reply)
            long_memory.store(user.id, f"用户: {user_text}\nAI: {ai_reply[:200]}")

            # 4g. 清理 Markdown 格式（Telegram 纯文本不渲染 Markdown）
            clean_reply = ai_reply
            clean_reply = re.sub(r'\*\*(.+?)\*\*', r'\1', clean_reply)  # **bold**
            clean_reply = re.sub(r'\*(.+?)\*', r'\1', clean_reply)      # *italic*
            clean_reply = re.sub(r'```[\s\S]*?```', lambda m: m.group().replace('```', ''), clean_reply)  # code blocks
            clean_reply = re.sub(r'`(.+?)`', r'\1', clean_reply)        # inline code
            clean_reply = re.sub(r'^#{1,6}\s+', '', clean_reply, flags=re.MULTILINE)  # headers
            # Telegram 消息限制 4096 字符
            if len(clean_reply) > 4000:
                clean_reply = clean_reply[:4000] + "\n\n...（内容过长已截断，请具体提问）"

            # 4h. 发送回复 + 内联按钮
            keyboard = build_call_human_markup(user.id)

            sent = await update.message.reply_text(
                clean_reply,
                reply_markup=keyboard,
                parse_mode=None,  # AI 回复不做HTML解析，避免标签冲突
            )

            # 4h. 同步 AI 回复到管理群 Forum Topic
            try:
                ai_admin_msg = await context.bot.send_message(
                    admin_group_id,
                    f"🤖 AI 回复:\n\n{ai_reply}",
                    message_thread_id=message_thread_id,
                )
                # 记录消息映射
                msg_map = MessageMap(
                    user_chat_message_id=sent.message_id,
                    group_chat_message_id=ai_admin_msg.message_id,
                    user_id=user.id,
                )
                db.add(msg_map)
                db.commit()
            except Exception as e:
                logger.error(f"同步 AI 回复到管理群失败: {e}")

        except Exception as e:
            logger.error(f"AI Pipeline 错误: {e}", exc_info=True)
            await update.message.reply_text(
                "⚠️ AI 暂时离线，请稍后重试或点击下方按钮呼叫人工客服。",
                reply_markup=build_call_human_markup(user.id),
            )
    else:
        # ═══ HUMAN_MODE: 仅转发，不回复 ═══
        # 消息已经在步骤3转发到管理群了，无需额外操作
        pass
