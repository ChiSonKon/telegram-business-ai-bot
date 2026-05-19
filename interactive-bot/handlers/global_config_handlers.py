"""管理后台：全局参数配置回调与文本输入。"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from .. import admin_user_ids
from ..global_settings import get_global_config, update_global_config

ADMIN_GLOBAL_CONFIG_STATE = "admin_global_config_state"

def get_global_config_keyboard(config: dict) -> InlineKeyboardMarkup:
    """生成全局配置面板键盘。"""
    
    def toggle_icon(val):
        return "✅" if val else "❌"
        
    buttons = [
        [
            InlineKeyboardButton(
                f"{toggle_icon(config.get('disable_captcha'))} 关闭验证码", 
                callback_data="globalcfg_toggle_disable_captcha"
            ),
            InlineKeyboardButton(
                f"{toggle_icon(config.get('is_delete_topic_as_ban_forever'))} 删帖即封禁", 
                callback_data="globalcfg_toggle_is_delete_topic_as_ban_forever"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{toggle_icon(config.get('is_delete_user_messages'))} 删帖清空消息", 
                callback_data="globalcfg_toggle_is_delete_user_messages"
            ),
        ],
        [
            InlineKeyboardButton(
                f"⏱ 发言间隔: {config.get('message_interval')}s", 
                callback_data="globalcfg_edit_message_interval"
            ),
            InlineKeyboardButton(
                f"🛡 限流: {config.get('rate_limit_per_minute')}/min", 
                callback_data="globalcfg_edit_rate_limit_per_minute"
            ),
        ],
        [InlineKeyboardButton("◀ 返回主菜单", callback_data="panel_main")],
    ]
    return InlineKeyboardMarkup(buttons)

async def callback_global_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin = query.from_user
    if admin.id not in admin_user_ids:
        await query.answer("No permission", show_alert=True)
        return

    action = query.data
    config = get_global_config()

    if action == "globalcfg_panel":
        text = (
            "⚙️ 全局参数设置 (实时生效)\n"
            "==============================\n\n"
            "点击下方按钮进行开关切换，或修改具体数值。"
        )
        await query.message.edit_text(text, reply_markup=get_global_config_keyboard(config))
        
    elif action.startswith("globalcfg_toggle_"):
        key = action.removeprefix("globalcfg_toggle_")
        if key in config:
            new_val = not config[key]
            update_global_config({key: new_val})
            config[key] = new_val
            await query.answer(f"已切换: {'开启' if new_val else '关闭'}")
            await query.message.edit_reply_markup(reply_markup=get_global_config_keyboard(config))
            
    elif action.startswith("globalcfg_edit_"):
        key = action.removeprefix("globalcfg_edit_")
        context.user_data[ADMIN_GLOBAL_CONFIG_STATE] = key
        
        names = {
            "message_interval": "发言间隔 (秒)",
            "rate_limit_per_minute": "每分钟限流次数",
        }
        name = names.get(key, key)
        
        await query.message.reply_text(
            f"请直接发送新的【{name}】数值 (必须是整数)。\n\n发送 /admin 可以取消修改。"
        )
        await query.answer()

async def handle_admin_global_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """接收管理员下一条私聊文本并保存为全局参数。"""
    user = update.effective_user
    if user.id not in admin_user_ids:
        return

    key = context.user_data.get(ADMIN_GLOBAL_CONFIG_STATE)
    if not key:
        return

    text = update.message.text or ""
    try:
        val = int(text.strip())
        update_global_config({key: val})
        saved = f"✅ 参数 {key} 已更新为 {val}。"
    except ValueError:
        saved = "❌ 必须输入整数数值，已取消修改。"

    context.user_data.pop(ADMIN_GLOBAL_CONFIG_STATE, None)
    
    config = get_global_config()
    await update.message.reply_text(
        f"{saved}\n\n⚙️ 当前全局参数设置：",
        reply_markup=get_global_config_keyboard(config),
    )
    raise ApplicationHandlerStop
