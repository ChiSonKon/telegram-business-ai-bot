"""
命令处理器 — /start, /admin, /clear, /broadcast, /status, /addadmin, /removeadmin
"""
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler
from telegram.helpers import mention_html

from db.database import SessionMaker
from db.model import MessageMap, User

from .. import (
    admin_group_id,
    admin_user_ids,
    app_name,
    logger,
)
from ..global_settings import get_global_setting
from ..middleware.state_machine import ChatMode, get_chat_mode
from ..start_settings import (
    build_start_bottom_markup,
    build_start_inline_markup,
    get_welcome_message,
)

db = SessionMaker()


def _get_admin_panel_keyboard():
    """管理面板键盘布局"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 系统状态", callback_data="panel_stats"),
            InlineKeyboardButton("👥 用户列表", callback_data="panel_users"),
        ],
        [
            InlineKeyboardButton("🤖 AI 设置", callback_data="panel_ai_settings"),
            InlineKeyboardButton("📝 提示词配置", callback_data="promptcfg_panel"),
        ],
        [
            InlineKeyboardButton("📋 管理群组", callback_data="panel_group"),
            InlineKeyboardButton("👑 管理员管理", callback_data="panel_admin_manage"),
        ],
        [
            InlineKeyboardButton("📤 广播中心", callback_data="panel_broadcast"),
            InlineKeyboardButton("\U0001F44B /start 设置", callback_data="panel_start_settings"),
        ],
        [
            InlineKeyboardButton("⚙️ 全局参数设置", callback_data="globalcfg_panel"),
        ],
        [
            InlineKeyboardButton("\U0001F504 全部重置为AI模式", callback_data="panel_reset_all_ai"),
        ],
    ])


async def _reply_start_for_user(update: Update, user):
    """按后台配置发送用户 /start 欢迎页，颜色字段不兼容时自动降级。"""
    text = f"{mention_html(user.id, user.full_name)}\n\n{get_welcome_message()}"
    inline_markup = build_start_inline_markup(user.id)
    bottom_markup = build_start_bottom_markup()

    try:
        await update.message.reply_html(text, reply_markup=inline_markup or bottom_markup)
        if inline_markup and bottom_markup:
            await update.message.reply_text("⌨️ 可使用下方快捷按钮继续操作。", reply_markup=bottom_markup)
    except BadRequest as e:
        logger.warning(f"Styled /start buttons failed, fallback without button colors: {e}")
        fallback_inline = build_start_inline_markup(user.id, include_style=False)
        fallback_bottom = build_start_bottom_markup(include_style=False)
        await update.message.reply_html(text, reply_markup=fallback_inline or fallback_bottom)
        if fallback_inline and fallback_bottom:
            await update.message.reply_text("⌨️ 可使用下方快捷按钮继续操作。", reply_markup=fallback_bottom)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    user = update.effective_user

    # 确保用户存在
    if not db.query(User).filter(User.user_id == user.id).first():
        u = User(
            user_id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            chat_mode="AI_MODE",
        )
        db.add(u)
        db.commit()

    if user.id in admin_user_ids:
        logger.info(f"{user.first_name}({user.id}) is admin")
        try:
            bg = await context.bot.get_chat(admin_group_id)
        except Exception as e:
            logger.error(f"admin group error {e}")
            await update.message.reply_text(
                f"Admin group error. Make sure the bot is in the group.\nError: {e}"
            )
            return ConversationHandler.END

        await update.message.reply_text(
            f"Welcome admin {user.first_name}!\n\n"
            f"Admin group: {bg.title}\n"
            f"Bot: @{context.bot.username}\n\n"
            f"Click below to open the control panel.",
            reply_markup=_get_admin_panel_keyboard(),
        )
    else:
        await _reply_start_for_user(update, user)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /admin 命令 — 管理后台面板"""
    user = update.effective_user
    if user.id not in admin_user_ids:
        await update.message.reply_text("No permission.")
        return

    total = db.query(User).count()
    ai_count = db.query(User).filter(User.chat_mode == "AI_MODE").count()
    human_count = db.query(User).filter(User.chat_mode == "HUMAN_MODE").count()

    text = (
        f"⚡ 白猫智能客服 - 管理后台\n"
        f"{'='*30}\n\n"
        f"👥 用户: {total}  |  🤖 AI: {ai_count}  |  👩‍💻 人工: {human_count}\n\n"
        f"选择功能模块："
    )

    await update.message.reply_text(
        text,
        reply_markup=_get_admin_panel_keyboard(),
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /clear 命令"""
    user = update.effective_user
    if user.id not in admin_user_ids:
        await update.message.reply_text("No permission.")
        return

    await context.bot.delete_forum_topic(
        update.effective_chat.id, update.message.message_thread_id
    )

    if not get_global_setting("is_delete_user_messages"):
        return

    target_user = db.query(User).filter(
        User.message_thread_id == update.message.message_thread_id
    ).first()
    if target_user:
        all_messages = db.query(MessageMap).filter(
            MessageMap.user_id == target_user.user_id
        ).all()
        try:
            await context.bot.delete_messages(
                target_user.user_id,
                [msg.user_chat_message_id for msg in all_messages],
            )
        except Exception as e:
            logger.error(f"Delete messages failed: {e}")


async def _broadcast_task(context: ContextTypes.DEFAULT_TYPE):
    """群发任务（后台异步执行，强制 3 秒间隔）"""
    msg_id, chat_id = context.job.data.split("_")
    context.job.data = {
        "from_chat_id": int(chat_id),
        "message_id": int(msg_id),
        "admin_chat_id": int(chat_id),
    }
    from .broadcast_handlers import _broadcast_job
    await _broadcast_job(context)


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /broadcast 命令"""
    user = update.effective_user
    if user.id not in admin_user_ids:
        await update.message.reply_text("No permission.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("Usage: Reply to a message and use /broadcast")
        return

    total_users = db.query(User).count()
    await update.message.reply_text(
        f"📤 Broadcast starting...\n"
        f"Target: {total_users} users\n"
        f"ETA: {total_users * 3} seconds\n"
        f"(3s interval for safety)"
    )

    context.job_queue.run_once(
        _broadcast_task,
        0,
        data=f"{update.message.reply_to_message.id}_{update.effective_chat.id}",
    )


async def broadcast_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /broadcast_all 命令 — 直接发文本群发"""
    user = update.effective_user
    if user.id not in admin_user_ids:
        await update.message.reply_text("No permission.")
        return

    text = update.message.text.replace("/broadcast_all", "", 1).strip()
    if not text:
        await update.message.reply_text("Usage: /broadcast_all [message text]")
        return

    users = db.query(User).all()
    total = len(users)
    await update.message.reply_text(
        f"📤 Broadcasting to {total} users..."
    )

    success = 0
    failed = 0
    for u in users:
        try:
            await context.bot.send_message(u.user_id, text)
            success += 1
        except Exception as e:
            failed += 1
            logger.warning(f"Broadcast to {u.user_id} failed: {e}")
        await asyncio.sleep(3)

    await update.message.reply_text(
        f"📊 Broadcast done: {success} ok, {failed} failed, {total} total"
    )


async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /addadmin 命令"""
    user = update.effective_user
    if user.id not in admin_user_ids:
        await update.message.reply_text("No permission.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /addadmin [user_id]")
        return

    try:
        new_admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user_id. Must be a number.")
        return

    if new_admin_id in admin_user_ids:
        await update.message.reply_text(f"{new_admin_id} is already an admin.")
        return

    admin_user_ids.append(new_admin_id)
    await update.message.reply_text(
        f"Admin {new_admin_id} added (runtime only).\n"
        f"To persist, also add to .env ADMIN_USER_IDS."
    )


async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /removeadmin 命令"""
    user = update.effective_user
    if user.id not in admin_user_ids:
        await update.message.reply_text("No permission.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /removeadmin [user_id]")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user_id.")
        return

    if target_id == user.id:
        await update.message.reply_text("Cannot remove yourself.")
        return

    if target_id in admin_user_ids:
        admin_user_ids.remove(target_id)
        await update.message.reply_text(
            f"Admin {target_id} removed (runtime only).\n"
            f"To persist, also update .env ADMIN_USER_IDS."
        )
    else:
        await update.message.reply_text(f"{target_id} is not an admin.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /status 命令"""
    user = update.effective_user
    if user.id not in admin_user_ids:
        await update.message.reply_text("No permission.")
        return

    if update.message.message_thread_id:
        target = db.query(User).filter(
            User.message_thread_id == update.message.message_thread_id
        ).first()
        if target:
            mode = get_chat_mode(target.user_id)
            await update.message.reply_text(
                f"User: {target.first_name} ({target.user_id})\n"
                f"Mode: {mode.value}\n"
                f"Username: @{target.username or 'N/A'}"
            )
            return

    total = db.query(User).count()
    await update.message.reply_text(
        f"Total users: {total}\n"
        f"Bot: @{context.bot.username}"
    )
