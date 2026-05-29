"""
TG Business 消息处理 — 监听 business_message 事件

当 Bot 被绑定到 Telegram Business 账号后，
通过该账号的私聊消息会通过 business_message 类型传入。
注意: business_message 通过 update.business_message 访问，而非 update.message
"""
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from db.database import SessionMaker
from db.model import User

from .. import (
    admin_group_id,
    admin_user_ids,
    blacklist_user_ids,
    logger,
    rate_limit_per_minute,
)
from ..llm.fallback_client import FallbackLLMClient
from ..llm.prompt_loader import load_system_prompt
from ..rag.retriever import search_knowledge
from ..memory.short_term import ShortTermMemory
from ..memory.long_term import LongTermMemory
from ..middleware.state_machine import is_ai_mode, ChatMode, set_chat_mode
from ..middleware.rate_limiter import RateLimiter
from .payment_rules import (
    build_paid_text_message,
    build_payment_media_message,
    build_payment_message,
    update_payment_context,
)
from .broadcast_handlers import upsert_business_contact

from ..start_settings import build_call_human_markup

# 单例
_llm_client = FallbackLLMClient()
_short_memory = ShortTermMemory(max_messages=15)
_long_memory = LongTermMemory()
_rate_limiter = RateLimiter(default_max=rate_limit_per_minute)

db = SessionMaker()


def _clean_markdown(text: str) -> str:
    """清理 Markdown 格式"""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'```[\s\S]*?```', lambda m: m.group().replace('```', ''), text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    return text


async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理 Telegram Business 接管的消息。

    关键: Business 消息通过 update.business_message 访问，不是 update.message!
    """
    # 关键修复: 使用 business_message 而非 message
    message = update.business_message
    if not message:
        message = update.message
    if not message:
        return

    user = message.from_user
    if not user:
        return

    # 关键: 跳过 Bot 自己发出的消息（防止死循环）
    if user.is_bot:
        return

    # 风控：跳过管理员的私人聊天（防止在老板私聊中乱回复）
    if user.id in admin_user_ids:
        return

    # 黑名单
    if user.id in blacklist_user_ids:
        return

    # 限流
    if not _rate_limiter.is_allowed(user.id):
        return  # Business 模式下静默丢弃

    user_text = message.text or message.caption or ""
    biz_conn_id = message.business_connection_id
    upsert_business_contact(message)

    # 付款截图/图片的两次确认流程：即使非文本消息也要处理
    if not user_text.strip():
        payment_media_reply = build_payment_media_message(context.user_data)
        if payment_media_reply:
            try:
                await context.bot.send_message(
                    chat_id=message.chat.id,
                    text=payment_media_reply,
                    business_connection_id=biz_conn_id,
                )
            except Exception as e:
                logger.warning(f"Business payment media reply failed: {e}")
        return  # 其他非文本消息不处理

    # 确保用户存在
    existing = db.query(User).filter(User.user_id == user.id).first()
    if not existing:
        u = User(
            user_id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            chat_mode="AI_MODE",
        )
        db.add(u)
        db.commit()

    # 检查是否要恢复 AI（必须在 is_ai_mode 检查之前，否则会被拦截）
    if user_text.strip() == "恢复AI" or user_text.strip() == "恢复ai":
        set_chat_mode(user.id, ChatMode.AI_MODE)
        biz_conn_id = message.business_connection_id
        try:
            await context.bot.send_message(
                chat_id=message.chat.id,
                text="🤖 AI 助手已重新上线！\n\n有什么问题尽管问我，如果需要人工随时发送“转人工”。",
                business_connection_id=biz_conn_id,
            )
        except Exception:
            pass
        return

    # 检查模式
    if not is_ai_mode(user.id):
        return  # HUMAN_MODE 下不触发 AI

    # Telegram Business 不支持 Callback 按钮，所以通过关键词触发转人工
    takeover_keywords = ["转人工", "呼叫人工", "呼叫主理人", "呼叫白猫", "人工客服", "售后"]
    if any(k in user_text for k in takeover_keywords):
        set_chat_mode(user.id, ChatMode.HUMAN_MODE)
        
        try:
            await context.bot.send_message(
                chat_id=message.chat.id,
                text="🔕 已为您呼叫主理人。\n\n请直接留下您的问题（如需求、报错等），主理人看到后会第一时间回复您！\n如需重新唤醒AI，请回复“恢复AI”。",
                business_connection_id=biz_conn_id,
            )
        except Exception:
            pass
            
        # 通知管理员群
        u = db.query(User).filter(User.user_id == user.id).first()
        if u and u.message_thread_id:
            try:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                from telegram.helpers import mention_html
                keyboard_admin = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🔕 接管对话", callback_data=f"admin_takeover_{user.id}"),
                        InlineKeyboardButton("🤖 恢复 AI", callback_data=f"admin_restore_{user.id}"),
                    ]
                ])
                await context.bot.send_message(
                    admin_group_id,
                    (
                        f"🚨🚨🚨 客户请求人工介入 🚨🚨🚨\n\n"
                        f"👤 {mention_html(user.id, user.full_name)}\n"
                        f"📱 {user.id}\n\n"
                        f"💬 客户说: {user_text}\n"
                    ),
                    message_thread_id=u.message_thread_id,
                    reply_markup=keyboard_admin,
                    parse_mode="HTML",
                )
            except Exception:
                pass
        return

    logger.info(f"Business msg from {user.first_name}({user.id}), chat={message.chat.id}, biz_conn={message.business_connection_id}")

    try:
        # AI Pipeline
        rag_context = search_knowledge(user_text)
        system_prompt = load_system_prompt()
        _short_memory.add(user.id, "user", user_text)
        messages = _short_memory.get_history(user.id)
        long_term_ctx = _long_memory.get_user_summary(user.id, user_text)

        ai_reply = await _llm_client.chat(
            system_prompt=system_prompt,
            messages=messages,
            rag_context=rag_context,
            long_term_context=long_term_ctx,
        )

        _short_memory.add(user.id, "assistant", ai_reply)
        _long_memory.store(user.id, f"User: {user_text}\nAI: {ai_reply[:200]}")

        # 清理 Markdown
        clean_reply = _clean_markdown(ai_reply)
        if len(clean_reply) > 4000:
            clean_reply = clean_reply[:4000] + "\n\n...(content truncated)"

        # Business 消息不支持 InlineKeyboard，只发文本
        # 尝试多种回复方式
        sent = False

        # 方式1: 通过 business_connection_id 发送
        if biz_conn_id and not sent:
            try:
                await context.bot.send_message(
                    chat_id=message.chat.id,
                    text=clean_reply,
                    business_connection_id=biz_conn_id,
                )
                sent = True
                logger.info(f"Business reply sent via biz_conn to {user.id}")
            except Exception as e1:
                logger.warning(f"biz_conn send failed: {e1}")

        # 方式2: 直接发给用户（通过 bot 私聊）
        if not sent:
            try:
                keyboard = build_call_human_markup(user.id)
                await context.bot.send_message(
                    chat_id=user.id,
                    text=clean_reply,
                    reply_markup=keyboard,
                )
                sent = True
                logger.info(f"Business reply sent via direct to {user.id}")
            except Exception as e2:
                logger.warning(f"Direct send also failed: {e2}")

    except Exception as e:
        logger.error(f"Business AI Pipeline error: {e}", exc_info=True)
