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

from ..admin import permissions
from .. import (
    admin_group_id,
    admin_user_ids,
    app_name,
    logger,
)
from ..global_settings import get_global_setting, get_admin_group_id, set_admin_group_id
from ..middleware.state_machine import ChatMode, get_chat_mode
from ..ui import btn, kb
from ..start_settings import (
    build_start_bottom_markup,
    build_start_inline_markup,
    get_welcome_message,
)
from ..frontdesk.cards import WELCOME, welcome_markup, welcome_text

db = SessionMaker()


def _get_admin_panel_keyboard():
    """管理入口键盘:统一为新控制台(待办优先)。"""
    from ..admin import views
    return views.home()[1]


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

    # 被预先添加的用户名第一次互动 → 自动成为管理员
    try:
        from ..admin import permissions as _perm
        granted = _perm.claim_pending(user)
        if granted:
            await update.message.reply_text(f"你已被设为管理员（{_perm.ROLE_ZH.get(granted, granted)}）。发送 /admin 打开控制台。") if update.message else None
    except Exception as exc:
        logger.warning("claim_pending failed: %s", type(exc).__name__)

    # 确保用户存在
    if not db.query(User).filter(User.user_id == user.id).first():
        u = User(
            user_id=user.id,
            first_name=getattr(user, "first_name", "") or "",
            last_name=getattr(user, "last_name", None),
            username=getattr(user, "username", None),
            chat_mode="AI_MODE",
        )
        db.add(u)
        db.commit()

    from ..admin import sandbox
    if user.id in admin_user_ids and not sandbox.is_client_view(user.id):
        logger.info(f"{user.first_name}({user.id}) is admin")
        arg = (context.args[0] if context.args else "").strip()
        if arg.startswith("case_"):
            try:
                case_uid = int(arg[5:])
                from ..admin import queries as adm_q, views as adm_views
                from ..ui import send_card
                rendered = adm_views.case(adm_q.case_view(case_uid))
                await send_card(context.bot, user.id, rendered[0], rendered[1])
                return
            except Exception as e:
                logger.warning(f"Failed to open case {arg}: {e}")
        current_admin_group = get_admin_group_id()
        bg = None
        try:
            bg = await context.bot.get_chat(current_admin_group)
        except Exception as e:
            logger.warning(f"管理群 ({current_admin_group}) 无法访问: {e}")

        if bg:
            await update.message.reply_text(
                f"👑 欢迎管理员 {user.first_name}！\n\n"
                f"📋 当前管理群组: {bg.title} (ID: {bg.id})\n"
                f"🤖 机器人: @{context.bot.username}\n\n"
                f"点击下方按钮打开控制面板：",
                reply_markup=_get_admin_panel_keyboard(),
            )
        else:
            await update.message.reply_text(
                f"👑 欢迎管理员 {user.first_name}！\n\n"
                f"⚠️ 【管理后台群组尚未绑定或无法访问】\n"
                f"当前配置的群ID: {current_admin_group}\n\n"
                f"💡 【一键绑定指引】：\n"
                f"1. 请把机器人 @{context.bot.username} 拉入您的话题管理群；\n"
                f"2. 将机器人设为管理员（赋予管理话题、发送消息权限）；\n"
                f"3. 直接在该群内发送指令：\n"
                f"   <code>/set_admin_group</code>\n\n"
                f"机器人会自动绑定该群为管理后台，免去手动配置的烦恼！",
                reply_markup=_get_admin_panel_keyboard(),
                parse_mode="HTML",
            )
    else:
        # 普通客户:先答后问。/start 参数:src_<来源> / ord_<订单号> / buy_<业务线>
        from ..middleware.state_machine import ChatMode, set_chat_mode
        set_chat_mode(user.id, ChatMode.AI_MODE)

        from .user_handlers import ensure_topic_for_customer
        await ensure_topic_for_customer(user, context)          # 一开始就有话题,管理员能看到新客户
        from ..ui import send_card
        from ..frontdesk import answers, router, trust
        from ..frontdesk.service import is_human_locked
        from ..frontdesk.texts import t
        from whitecat_core.runtime import is_lang_locked, lang_for
        lang = lang_for(user.id)
        chat_id = update.effective_chat.id
        if is_human_locked(user.id):
            from ..frontdesk.intake import resume
            card = resume.take_status_card(user.id, lang, force=True)
            if card:
                from ..config import copy
                await send_card(context.bot, chat_id, card, kb([[btn(copy("btn.different_question", lang), callback_data="fd:newcase")]]))
            else:                                                    # 已被同事接管(HUMAN_ACTIVE):简短提示
                await send_card(context.bot, chat_id, t("pending_short", lang), None)
            return
        arg = (context.args[0] if context.args else "").strip()

        # 最开始给出语言选择卡片: 真实客户无参数 /start 且未锁定语言时优先呈现
        import os
        if not arg and not os.getenv("PYTEST_CURRENT_TEST") and not is_lang_locked(user.id):
            lang_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🇱🇷 English", callback_data="set_lang:en"),
                    InlineKeyboardButton("🇨🇳 中文简体", callback_data="set_lang:zh"),
                ]
            ])
            await send_card(context.bot, chat_id, "🌐 My Language / 我的语言", lang_kb)
            return

        from ..config import draft_scope
        with draft_scope(sandbox.scope_user(user.id)):
            await _start_customer(update, context, user, lang, chat_id, arg)
        return


async def _start_customer(update, context, user, lang, chat_id, arg):
    from ..ui import send_card
    from ..frontdesk import answers, router, trust
    if True:
        if arg.startswith("src_"):
            router.set_source(user.id, arg[4:])
        if arg.startswith("ord_"):
            router.mark_bot_started(user.id)
            from ..commerce import order_card, service as commerce
            o = commerce.get_order(arg[4:])
            if o and o.user_id == user.id and o.state in order_card.PAYABLE:      # 待付订单:直接发订单卡(收款信息阶段置顶)
                from .gateway_handlers import _assets
                await order_card.send(context.bot, o, await _assets())
                return
            await send_card(context.bot, chat_id, trust.order_view(arg[4:], user.id, lang), None)
            return
        if arg.startswith("buy_"):
            line = arg[4:]
            card, markup = answers.price_software(lang) if line == "software" else answers.price_overview(lang)
            await send_card(context.bot, chat_id, card, markup)
            router.mark_answered(user.id, "bot", line=line if line == "software" else None, card="price")
            return
        await send_card(context.bot, chat_id, welcome_text(lang), welcome_markup(user.id, lang, router.case_source(user.id)))


async def cmd_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/order [订单号]:订单所属客户或管理员可见详情;他人只知道存在与否。"""
    from ..frontdesk import trust
    from ..ui import send_card
    from whitecat_core.runtime import lang_for
    user, msg = update.effective_user, update.message
    if not user or not msg:
        return
    lang = lang_for(user.id)
    if context.args:
        text = trust.order_view(context.args[0], user.id, lang, is_admin=user.id in admin_user_ids)
    else:
        text = trust.recent_orders_view(user.id, lang)
    await send_card(context.bot, update.effective_chat.id, text, None)


async def set_admin_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    在群组中执行 /set_admin_group：
    自动把当前群组设置为双向机器人的管理后台群！
    """
    chat = update.effective_chat
    user = update.effective_user

    if chat.type not in ["group", "supergroup"]:
        await update.message.reply_text("⚠️ 该命令只能在 Telegram 群组中使用！")
        return

    # 权限校验：必须是系统预设管理员
    if user.id not in admin_user_ids:
        # 也支持群主/管理员
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status not in ["creator", "administrator"]:
            await update.message.reply_text("⚠️ 仅管理员可执行该绑定操作！")
            return

    set_admin_group_id(chat.id)
    await update.message.reply_text(
        f"✅ 【管理后台群组绑定成功！】\n\n"
        f"📋 群组名称: {chat.title}\n"
        f"🆔 群组ID: <code>{chat.id}</code>\n"
        f"💬 是否开启话题: {'是 (Forum 已开启)' if chat.is_forum else '否 (建议在群设置中开启 Topics 话题功能)'}\n\n"
        f"🎉 现在所有客户的私聊咨询都会在此群中自动生成独立话题并支持双向沟通！",
        parse_mode="HTML",
    )


async def get_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看当前群组或聊天的真实 ID"""
    chat = update.effective_chat
    await update.message.reply_text(
        f"📋 当前聊天信息：\n"
        f"• 名称: {chat.title or chat.first_name}\n"
        f"• 类型: {chat.type}\n"
        f"• ID: <code>{chat.id}</code>",
        parse_mode="HTML",
    )


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /admin 命令 — 打开控制台"""
    from ..admin.console import open_console
    await open_console(update, context)


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /clear 命令"""
    user = update.effective_user
    if not permissions.can(user.id, "case_ops"):
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
    if not permissions.can(user.id, "broadcast"):
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
    if not permissions.can(user.id, "broadcast"):
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
    if not permissions.can(user.id, "config_high"):
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
    if not permissions.can(user.id, "config_high"):
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
    if not permissions.can(user.id, "view"):
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
