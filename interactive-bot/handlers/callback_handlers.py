"""
回调处理器 — 处理内联按钮点击事件

1. 客户侧: [呼叫老板/转人工] / [恢复AI对话]
2. 管理侧: [接管对话] / [恢复 AI]
3. 验证码回调
"""
import os
import random
import time
from string import ascii_letters as letters

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from telegram.helpers import mention_html

from db.database import SessionMaker
from db.model import User

from .. import admin_group_id, admin_user_ids, logger, disable_captcha
from ..utils import delete_message_later
from ..start_settings import build_call_human_markup
from ..middleware.state_machine import ChatMode, set_chat_mode, get_chat_mode

db = SessionMaker()


# ==========================================
# 客户侧：转人工
# ==========================================

async def callback_call_human(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """客户点击 [呼叫老板/转人工] 按钮"""
    query = update.callback_query
    user = query.from_user

    # 提取 user_id（确保是本人点击）
    target_user_id = int(query.data.split("_")[-1])
    if user.id != target_user_id:
        await query.answer("This is not your button", show_alert=True)
        return

    await query.answer("Calling boss...")

    # 切换为人工模式
    set_chat_mode(user.id, ChatMode.HUMAN_MODE)

    # 回复客户 — 附带 [恢复AI] 按钮
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🤖 恢复AI对话",
            callback_data=f"restore_ai_{user.id}",
        )]
    ])

    await query.message.reply_text(
        "已呼叫主理人，请稍候...\n\n"
        "主理人看到后会第一时间回复你\n"
        "在此期间你可以继续发送消息，我会帮你转达。\n\n"
        "如果想重新使用 AI 对话，点击下方按钮即可。",
        reply_markup=keyboard,
    )

    # 在管理群 Forum Topic 发送强提醒
    u = db.query(User).filter(User.user_id == user.id).first()
    if u and u.message_thread_id:
        keyboard_admin = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔕 接管对话", callback_data=f"admin_takeover_{user.id}"),
                InlineKeyboardButton("🤖 恢复 AI", callback_data=f"admin_restore_{user.id}"),
            ]
        ])

        await context.bot.send_message(
            admin_group_id,
            (
                f"🚨🚨🚨 客户呼叫人工 🚨🚨🚨\n\n"
                f"👤 {mention_html(user.id, user.full_name)}\n"
                f"📱 {user.id}\n\n"
                f"请尽快回复！"
            ),
            message_thread_id=u.message_thread_id,
            reply_markup=keyboard_admin,
            parse_mode="HTML",
        )


# ==========================================
# 客户侧：自行恢复 AI
# ==========================================

async def callback_restore_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """客户点击 [恢复AI对话] 按钮"""
    query = update.callback_query
    user = query.from_user

    target_user_id = int(query.data.split("_")[-1])
    if user.id != target_user_id:
        await query.answer("This is not your button", show_alert=True)
        return

    set_chat_mode(user.id, ChatMode.AI_MODE)
    await query.answer("AI mode restored!")

    keyboard = build_call_human_markup(user.id)

    await query.message.reply_text(
        "🤖 AI 已重新上线！\n\n有什么问题尽管问我，需要人工服务随时点击下方按钮。",
        reply_markup=keyboard,
    )

    # 通知管理群
    u = db.query(User).filter(User.user_id == user.id).first()
    if u and u.message_thread_id:
        try:
            await context.bot.send_message(
                admin_group_id,
                f"🤖 客户 {mention_html(user.id, user.full_name)} 已自行恢复 AI 模式",
                message_thread_id=u.message_thread_id,
                parse_mode="HTML",
            )
        except Exception:
            pass


# ==========================================
# 管理侧：接管对话
# ==========================================

async def callback_admin_takeover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理员点击 [接管对话] 按钮"""
    query = update.callback_query
    admin = query.from_user

    if admin.id not in admin_user_ids:
        await query.answer("No permission", show_alert=True)
        return

    target_user_id = int(query.data.split("_")[-1])
    set_chat_mode(target_user_id, ChatMode.HUMAN_MODE)

    await query.answer("Conversation taken over")
    await query.message.reply_text(
        f"🔕 管理员 {admin.first_name} 已接管对话\n"
        f"AI 已关闭，后续消息将直接转发给客户。"
    )


# ==========================================
# 管理侧：恢复 AI
# ==========================================

async def callback_admin_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理员点击 [恢复 AI] 按钮"""
    query = update.callback_query
    admin = query.from_user

    if admin.id not in admin_user_ids:
        await query.answer("No permission", show_alert=True)
        return

    target_user_id = int(query.data.split("_")[-1])
    set_chat_mode(target_user_id, ChatMode.AI_MODE)

    await query.answer("AI restored")
    await query.message.reply_text(
        f"🤖 管理员 {admin.first_name} 已恢复 AI 模式\n"
        f"后续消息将由 AI 自动回复。"
    )

    # 通知客户
    try:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "👩‍💻 呼叫老板/转人工",
                callback_data=f"call_human_{target_user_id}",
            )]
        ])
        await context.bot.send_message(
            target_user_id,
            "🤖 AI 助手已重新上线！\n\n有什么问题继续问我，需要人工服务随时点按钮。",
            reply_markup=keyboard,
        )
    except Exception:
        pass


# ==========================================
# 管理面板回调
# ==========================================

async def callback_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """管理面板按钮回调"""
    query = update.callback_query
    admin = query.from_user

    if admin.id not in admin_user_ids:
        await query.answer("No permission", show_alert=True)
        return

    action = query.data

    if action == "panel_stats":
        total = db.query(User).count()
        from ..middleware.state_machine import ChatMode as CM
        ai_count = db.query(User).filter(User.chat_mode == "AI_MODE").count()
        human_count = db.query(User).filter(User.chat_mode == "HUMAN_MODE").count()

        from .. import rate_limit_per_minute, bot_username
        from ..ai_settings import get_primary_ai_config

        primary_ai = get_primary_ai_config()

        await query.message.edit_text(
            f"📊 系统状态面板\n"
            f"{'='*30}\n\n"
            f"👥 总用户数: {total}\n"
            f"🤖 AI 模式用户: {ai_count}\n"
            f"👩‍💻 人工模式用户: {human_count}\n\n"
            f"🔧 当前配置:\n"
            f"  Bot: @{bot_username}\n"
            f"  首位AI: {primary_ai['name']}\n"
            f"  模型: {primary_ai['model']}\n"
            f"  API: {primary_ai['base_url']}\n"
            f"  限流: {rate_limit_per_minute} 次/分钟\n\n"
            f"👑 管理员: {', '.join(str(x) for x in admin_user_ids)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀ 返回主菜单", callback_data="panel_main")]
            ]),
        )

    elif action == "panel_group":
        try:
            chat = await context.bot.get_chat(admin_group_id)
            group_info = (
                f"📋 管理群组信息\n"
                f"{'='*30}\n\n"
                f"名称: {chat.title}\n"
                f"ID: {admin_group_id}\n"
                f"类型: {chat.type}\n"
                f"成员数: {await chat.get_member_count()}"
            )
        except Exception as e:
            group_info = f"获取群组信息失败: {e}"

        await query.message.edit_text(
            group_info,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀ 返回主菜单", callback_data="panel_main")]
            ]),
        )

    elif action == "panel_broadcast":
        from .broadcast_handlers import get_broadcast_keyboard, get_broadcast_panel_text

        await query.message.edit_text(
            get_broadcast_panel_text(),
            reply_markup=get_broadcast_keyboard(),
        )

    elif action == "panel_users":
        users = db.query(User).order_by(User.id.desc()).limit(15).all()
        lines = [f"👥 最近用户列表 (最新15位)\n{'='*30}\n"]
        for u in users:
            mode_icon = "🤖" if u.chat_mode == "AI_MODE" else "👩‍💻"
            premium = "⭐" if u.is_premium else ""
            lines.append(
                f"{mode_icon}{premium} {u.first_name or '?'} | "
                f"@{u.username or '-'} | {u.user_id}"
            )
        await query.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 刷新", callback_data="panel_users")],
                [InlineKeyboardButton("◀ 返回主菜单", callback_data="panel_main")]
            ]),
        )

    elif action == "panel_ai_settings":
        from .ai_config_handlers import get_ai_config_keyboard
        from ..ai_settings import get_ai_config_text

        await query.message.edit_text(
            get_ai_config_text(),
            reply_markup=get_ai_config_keyboard(),
        )

    elif action == "panel_admin_manage":
        admin_list = "\n".join(f"  👑 {uid}" for uid in admin_user_ids)
        await query.message.edit_text(
            f"👥 管理员管理\n"
            f"{'='*30}\n\n"
            f"当前管理员:\n{admin_list}\n\n"
            f"添加管理员:\n"
            f"  /addadmin [user_id]\n\n"
            f"移除管理员:\n"
            f"  /removeadmin [user_id]\n\n"
            f"注: 在 .env 中配置的是超级管理员，不可通过命令移除。",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀ 返回主菜单", callback_data="panel_main")]
            ]),
        )

    elif action == "panel_start_settings":
        from .start_config_handlers import get_start_config_keyboard
        from ..start_settings import get_start_config_text

        await query.message.edit_text(
            get_start_config_text(),
            reply_markup=get_start_config_keyboard(),
            parse_mode="HTML"
        )

    elif action == "panel_reset_all_ai":
        # 重置所有用户为 AI 模式
        db.query(User).filter(User.chat_mode == "HUMAN_MODE").update(
            {"chat_mode": "AI_MODE"}
        )
        db.commit()
        await query.answer("All users reset to AI mode!")
        await query.message.edit_text(
            "已将所有用户重置为 AI 模式。",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀ 返回主菜单", callback_data="panel_main")]
            ]),
        )

    elif action == "panel_main":
        from .command_handlers import _get_admin_panel_keyboard
        await query.message.edit_text(
            _get_admin_panel_text(),
            reply_markup=_get_admin_panel_keyboard(),
        )

    await query.answer()


def _get_admin_panel_text():
    return (
        "⚡ 白猫智能客服 - 管理后台\n"
        f"{'='*30}\n\n"
        "选择功能模块："
    )


# ==========================================
# 验证码回调
# ==========================================

async def check_human(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """人机验证"""
    user = update.effective_user
    if context.user_data.get("is_human", False) is False:
        if context.user_data.get("is_human_error_time", 0) > time.time() - 120:
            await update.message.reply_html("你已经被禁言,请稍后再尝试。")
            return False
        file_name = random.choice(os.listdir("./assets/imgs"))
        code = file_name.replace("image_", "").replace(".png", "")
        file = f"./assets/imgs/{file_name}"
        codes = ["".join(random.sample(letters, 5)) for _ in range(0, 7)]
        codes.append(code)
        random.shuffle(codes)

        photo = context.bot_data.get(f"image|{code}")
        if not photo:
            photo = file
        buttons = [
            InlineKeyboardButton(x, callback_data=f"vcode_{x}_{user.id}") for x in codes
        ]
        button_matrix = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
        sent = await update.message.reply_photo(
            photo,
            f"{mention_html(user.id, user.first_name)}请选择图片中的文字。回答错误将无法联系客服。",
            reply_markup=InlineKeyboardMarkup(button_matrix),
            parse_mode="HTML",
        )
        biggest_photo = sorted(sent.photo, key=lambda x: x.file_size, reverse=True)[0]
        context.bot_data[f"image|{code}"] = biggest_photo.file_id
        context.user_data["vcode"] = code
        await delete_message_later(60, sent.chat.id, sent.message_id, context)
        return False
    return True


async def callback_query_vcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """验证码按钮回调"""
    query = update.callback_query
    user = query.from_user
    code = query.data.split("_")[1]
    user_id = query.data.split("_")[2]
    if user_id == str(user.id):
        if code == context.user_data.get("vcode"):
            await query.answer("Correct!")
            sent = await context.bot.send_message(
                update.effective_chat.id,
                f"{mention_html(user.id, user.first_name)} , welcome.",
                parse_mode="HTML",
            )
            context.user_data["is_human"] = True
        else:
            await query.answer("Wrong, muted 2 minutes")
            context.user_data["is_human_error_time"] = time.time()
    await query.message.delete()
