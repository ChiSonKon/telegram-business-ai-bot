"""
管理侧消息处理 — 管理群 Forum Topic → 客户

处理管理员在 Forum Topic 中的回复，转发给对应客户。
"""
import asyncio

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from db.database import SessionMaker
from db.model import FormnStatus, MediaGroupMesssage, MessageMap, User

from .. import admin_group_id, admin_user_ids, logger
from ..middleware.state_machine import ChatMode, get_chat_mode, set_chat_mode

db = SessionMaker()


# ═══ 延时发送媒体组消息 ═══

async def _send_media_group_later(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    media_group_id = job.data
    _, from_chat_id, target_id, dir = job.name.split("_")

    media_group_msgs = (
        db.query(MediaGroupMesssage)
        .filter(
            MediaGroupMesssage.media_group_id == media_group_id,
            MediaGroupMesssage.chat_id == from_chat_id,
        )
        .all()
    )
    chat = await context.bot.get_chat(target_id)
    if dir == "u2a":
        u = db.query(User).filter(User.user_id == from_chat_id).first()
        message_thread_id = u.message_thread_id
        sents = await chat.send_copies(
            from_chat_id,
            [m.message_id for m in media_group_msgs],
            message_thread_id=message_thread_id,
        )
        for sent, msg in zip(sents, media_group_msgs):
            msg_map = MessageMap(
                user_chat_message_id=msg.message_id,
                group_chat_message_id=sent.message_id,
                user_id=u.user_id,
            )
            db.add(msg_map)
            db.commit()
    else:
        sents = await chat.send_copies(
            from_chat_id, [m.message_id for m in media_group_msgs]
        )
        for sent, msg in zip(sents, media_group_msgs):
            msg_map = MessageMap(
                user_chat_message_id=sent.message_id,
                group_chat_message_id=msg.message_id,
                user_id=target_id,
            )
            db.add(msg_map)
            db.commit()


async def send_media_group_later(
    delay: float, chat_id, target_id, media_group_id: int, dir, context: ContextTypes.DEFAULT_TYPE
):
    name = f"sendmediagroup_{chat_id}_{target_id}_{dir}"
    context.job_queue.run_once(
        _send_media_group_later, delay, chat_id=chat_id, name=name, data=media_group_id
    )
    return name


async def forwarding_message_a2u(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    管理侧消息处理器。
    管理员在 Forum Topic 中的消息 → 转发给对应客户。
    """
    message_thread_id = update.message.message_thread_id
    if not message_thread_id:
        # General 消息，忽略
        return

    u = db.query(User).filter(User.message_thread_id == message_thread_id).first()
    if not u:
        logger.debug(f"未找到 thread {message_thread_id} 对应的用户")
        return

    user_id = u.user_id

    # 处理 Forum Topic 状态事件
    if update.message.forum_topic_created:
        f = FormnStatus(
            message_thread_id=update.message.message_thread_id, status="opened"
        )
        db.add(f)
        db.commit()
        return

    if update.message.forum_topic_closed:
        await context.bot.send_message(
            user_id, "对话已经结束。对方已经关闭了对话。你的留言将被忽略。"
        )
        f = db.query(FormnStatus).filter(
            FormnStatus.message_thread_id == update.message.message_thread_id
        ).first()
        if f:
            f.status = "closed"
            db.add(f)
            db.commit()
        return

    if update.message.forum_topic_reopened:
        await context.bot.send_message(user_id, "对方重新打开了对话。可以继续对话了。")
        f = db.query(FormnStatus).filter(
            FormnStatus.message_thread_id == update.message.message_thread_id
        ).first()
        if f:
            f.status = "opened"
            db.add(f)
            db.commit()
        return

    # 检查 Topic 是否关闭
    f = db.query(FormnStatus).filter(
        FormnStatus.message_thread_id == message_thread_id
    ).first()
    if f and f.status == "closed":
        await update.message.reply_html("对话已经结束。希望和对方联系，需要打开对话。")
        return

    # 自动接管逻辑：如果当前是 AI 模式，管理员回话自动转人工
    current_mode = get_chat_mode(user_id)
    if current_mode == ChatMode.AI_MODE:
        set_chat_mode(user_id, ChatMode.HUMAN_MODE)
        from ..admin.actions import take_over
        take_over(update.effective_user.id, user_id)          # 同步前台状态并记入操作记录
        from ..timeout_tracker import dismiss_care
        dismiss_care(user_id)
        # 提示管理员
        await update.message.reply_html(
            f"🔕 <b>管理员 {update.effective_user.first_name} 已接管对话</b>\nAI 已关闭，后续消息将直接转发给客户。"
        )

    # 待跟进的客户:同事一回话就算接手(此前聊天模式已是人工时不会触发上面的接管,导致控制台一直显示"待跟进")
    from ..frontdesk.service import case_state
    if case_state(user_id) == "HUMAN_PENDING":
        from ..admin.actions import take_over as _take_over
        _take_over(update.effective_user.id, user_id)

    # 对同事的消息点一下 ❤️ 表示"已收到并转给客户",约 1.5 秒后自动取消
    try:
        from ..experience import reactions
        await reactions.flash(context.bot, update.effective_chat.id, update.message.message_id, "❤️")
    except Exception as re_err:
        logger.debug(f"对管理消息点赞失败: {re_err}")

    chat_id = user_id

    # 构筑发送参数
    params = {}
    if update.message.reply_to_message:
        reply_in_admin = update.message.reply_to_message.message_id
        msg_map = db.query(MessageMap).filter(
            MessageMap.group_chat_message_id == reply_in_admin
        ).first()
        if msg_map:
            params["reply_to_message_id"] = msg_map.user_chat_message_id

    try:
        # 处理媒体组
        if update.message.media_group_id:
            msg = MediaGroupMesssage(
                chat_id=update.message.chat.id,
                message_id=update.message.message_id,
                media_group_id=update.message.media_group_id,
                is_header=False,
                caption_html=update.message.caption_html,
            )
            db.add(msg)
            db.commit()
            if update.message.media_group_id != context.application.user_data.get(
                user_id, {}
            ).get("current_media_group_id", 0):
                if user_id not in context.application.user_data:
                    context.application.user_data[user_id] = {}
                context.application.user_data[user_id][
                    "current_media_group_id"
                ] = update.message.media_group_id
                await send_media_group_later(
                    5,
                    update.effective_chat.id,
                    user_id,
                    update.message.media_group_id,
                    "a2u",
                    context,
                )
            return

        # 发送给用户（兼容 Telegram Business 客户）
        sent_msg = None
        from db.model import BusinessContact
        b_contact = db.query(BusinessContact).filter(BusinessContact.user_id == user_id).first()
        if b_contact and b_contact.business_connection_id:
            try:
                if update.message.text:
                    sent_msg = await context.bot.send_message(
                        chat_id=b_contact.chat_id or user_id,
                        text=update.message.text,
                        business_connection_id=b_contact.business_connection_id,
                    )
            except Exception as be:
                logger.warning(f"Business connection 转发失败，降级常规发送: {be}")

        if not sent_msg:
            chat = await context.bot.get_chat(chat_id)
            sent_msg = await chat.send_copy(
                update.effective_chat.id, update.message.id, **params
            )

        msg_map = MessageMap(
            group_chat_message_id=update.message.id,
            user_chat_message_id=sent_msg.message_id,
            user_id=user_id,
        )
        db.add(msg_map)
        db.commit()
        from ..frontdesk.service import record_human_reply
        record_human_reply(user_id)
        _txt = update.message.text or update.message.caption
        if _txt:
            from ..memory.conversation_store import ConversationStore
            ConversationStore().add(user_id, "staff", _txt)

    except Exception as e:
        await update.message.reply_html(f"发送失败: {e}\n")
