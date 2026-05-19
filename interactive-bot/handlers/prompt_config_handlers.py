"""管理后台：系统提示词 (System Prompt) 配置回调与文本输入。"""
from __future__ import annotations

import html
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from .. import admin_user_ids
from ..prompt_settings import get_system_prompt, set_system_prompt

ADMIN_PROMPT_CONFIG_STATE = "admin_prompt_config_state"

def get_prompt_config_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ 修改提示词", callback_data="promptcfg_edit")],
        [InlineKeyboardButton("◀ 返回主菜单", callback_data="panel_main")],
    ])

async def callback_prompt_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin = query.from_user
    if admin.id not in admin_user_ids:
        await query.answer("No permission", show_alert=True)
        return

    action = query.data
    if action == "promptcfg_panel":
        current_prompt = get_system_prompt()
        display_prompt = html.escape(current_prompt)
        if len(display_prompt) > 3000:
            display_prompt = display_prompt[:3000] + "\n... (内容过长已截断)"
            
        text = (
            "📝 系统提示词 (System Prompt) 设置\n"
            "==============================\n\n"
            "当前提示词：\n"
            f"<pre>{display_prompt}</pre>\n"
        )
        await query.message.edit_text(
            text, 
            reply_markup=get_prompt_config_keyboard(),
            parse_mode="HTML"
        )
    elif action == "promptcfg_edit":
        context.user_data[ADMIN_PROMPT_CONFIG_STATE] = "edit"
        await query.message.reply_text(
            "请直接发送新的系统提示词 (System Prompt)。\n"
            "支持多行文本；发送后会立即保存并实时生效。\n\n"
            "发送 /admin 可以取消修改。"
        )
    await query.answer()


async def handle_admin_prompt_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """接收管理员下一条私聊文本并保存为系统提示词。"""
    user = update.effective_user
    if user.id not in admin_user_ids:
        return

    state = context.user_data.get(ADMIN_PROMPT_CONFIG_STATE)
    if not state:
        return

    text = update.message.text or ""
    if state == "edit":
        if text.strip():
            set_system_prompt(text)
            saved = "✅ 系统提示词已保存并实时生效。"
        else:
            saved = "❌ 提示词不能为空，已取消修改。"
    else:
        saved = "未知配置状态，已取消。"

    context.user_data.pop(ADMIN_PROMPT_CONFIG_STATE, None)
    
    current_prompt = get_system_prompt()
    display_prompt = html.escape(current_prompt)
    if len(display_prompt) > 3000:
        display_prompt = display_prompt[:3000] + "\n... (内容过长已截断)"
        
    await update.message.reply_html(
        f"{saved}\n\n📝 当前提示词：\n<pre>{display_prompt}</pre>",
        reply_markup=get_prompt_config_keyboard(),
    )
    raise ApplicationHandlerStop
