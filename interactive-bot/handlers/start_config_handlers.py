"""管理后台：/start 欢迎语与按钮配置回调。"""
from __future__ import annotations

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ApplicationHandlerStop, ContextTypes

from .. import admin_user_ids, logger
from ..start_settings import (
    build_start_bottom_markup,
    build_start_inline_markup,
    get_button_config_help,
    get_start_config_text,
    get_welcome_message,
    parse_button_config,
    set_bottom_buttons,
    set_inline_buttons,
    set_welcome_message,
)


ADMIN_CONFIG_STATE = "admin_start_config_state"


def get_start_config_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ 修改欢迎消息", callback_data="startcfg_edit_welcome")],
        [InlineKeyboardButton("🔳 设置透明按钮", callback_data="startcfg_edit_inline")],
        [InlineKeyboardButton("⌨️ 设置底部按钮", callback_data="startcfg_edit_bottom")],
        [InlineKeyboardButton("👁 预览 /start", callback_data="startcfg_preview")],
        [InlineKeyboardButton("◀ 返回主菜单", callback_data="panel_main")],
    ])


async def callback_start_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin = query.from_user
    if admin.id not in admin_user_ids:
        await query.answer("No permission", show_alert=True)
        return

    action = query.data
    if action == "startcfg_panel":
        await query.message.edit_text(get_start_config_text(), reply_markup=get_start_config_keyboard(), parse_mode="HTML")
    elif action == "startcfg_edit_welcome":
        context.user_data[ADMIN_CONFIG_STATE] = "welcome"
        await query.message.reply_text(
            "请直接发送新的 /start 欢迎消息。\n"
            "支持多行文本；发送后会立即保存。"
        )
    elif action == "startcfg_edit_inline":
        context.user_data[ADMIN_CONFIG_STATE] = "inline"
        await query.message.reply_text(get_button_config_help("inline"))
    elif action == "startcfg_edit_bottom":
        context.user_data[ADMIN_CONFIG_STATE] = "bottom"
        await query.message.reply_text(get_button_config_help("bottom"))
    elif action == "startcfg_preview":
        await _send_start_preview(query.message, context, admin.id, admin.full_name)
    await query.answer()


async def handle_admin_start_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """接收管理员下一条私聊文本并保存为配置。"""
    user = update.effective_user
    if user.id not in admin_user_ids:
        return

    state = context.user_data.get(ADMIN_CONFIG_STATE)
    logger.info(f"[Admin Input] User {user.id} state is: {state}")
    
    if not state:
        return

    text = update.message.text or ""
    try:
        if state == "welcome":
            set_welcome_message(update.message.text_html or text)
            saved = "✅ 欢迎消息已保存。"
        elif state == "inline":
            set_inline_buttons(parse_button_config(update.message, "inline"))
            saved = "✅ 透明按钮已保存。"
        elif state == "bottom":
            set_bottom_buttons(parse_button_config(update.message, "bottom"))
            saved = "✅ 底部按钮已保存。"
        else:
            saved = "未知配置状态，已取消。"
    except ValueError as e:
        await update.message.reply_text(f"❌ 保存失败：{e}\n\n请按提示格式重新发送，或发送 /admin 返回后台。")
        raise ApplicationHandlerStop

    context.user_data.pop(ADMIN_CONFIG_STATE, None)
    try:
        await update.message.reply_html(
            f"{saved}\n\n{get_start_config_text()}",
            reply_markup=get_start_config_keyboard(),
        )
    except Exception as e:
        logger.error(f"reply_html failed: {e}")
        await update.message.reply_text(
            f"{saved}\n\n{get_start_config_text()}",
            reply_markup=get_start_config_keyboard(),
        )
    raise ApplicationHandlerStop


async def _send_start_preview(message, context: ContextTypes.DEFAULT_TYPE, user_id: int, full_name: str):
    text = f"<a href=\"tg://user?id={user_id}\">{html.escape(full_name)}</a>\n\n{get_welcome_message()}"
    inline_markup = build_start_inline_markup(user_id)
    bottom_markup = build_start_bottom_markup()

    try:
        await message.reply_html(text, reply_markup=inline_markup or bottom_markup)
        if inline_markup and bottom_markup:
            await message.reply_text("⌨️ 底部按钮预览", reply_markup=bottom_markup)
    except BadRequest as e:
        logger.warning(f"Styled start preview failed, fallback without button colors: {e}")
        await message.reply_html(text, reply_markup=build_start_inline_markup(user_id, include_style=False) or build_start_bottom_markup(False))
        if inline_markup and bottom_markup:
            await message.reply_text("⌨️ 底部按钮预览", reply_markup=build_start_bottom_markup(False))