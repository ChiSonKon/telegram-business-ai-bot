"""管理后台：AI 引擎配置回调与文本输入。"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from .. import admin_user_ids
from ..ai_settings import (
    add_ai_config,
    delete_ai_config,
    get_ai_config_help,
    get_ai_config_text,
    get_ai_configs,
    parse_ai_config_line,
    parse_ai_configs_bulk,
    promote_ai_config,
    set_ai_configs,
    toggle_ai_config,
)


ADMIN_AI_CONFIG_STATE = "admin_ai_config_state"


def get_ai_config_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("➕ 新增AI", callback_data="aicfg_add")],
        [InlineKeyboardButton("🥇 选择首位AI", callback_data="aicfg_select")],
        [InlineKeyboardButton("🧩 批量覆盖配置", callback_data="aicfg_bulk")],
    ]
    for cfg in get_ai_configs():
        status = "停用" if cfg.get("enabled") else "启用"
        buttons.append([
            InlineKeyboardButton(f"{status} {cfg['name']}", callback_data=f"aicfg_toggle_{cfg['id']}"),
            InlineKeyboardButton(f"删除 {cfg['name']}", callback_data=f"aicfg_delete_{cfg['id']}"),
        ])
    buttons.append([InlineKeyboardButton("🔄 刷新", callback_data="aicfg_panel")])
    buttons.append([InlineKeyboardButton("◀ 返回主菜单", callback_data="panel_main")])
    return InlineKeyboardMarkup(buttons)


def get_ai_select_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for cfg in get_ai_configs():
        icon = "✅" if cfg.get("enabled") else "⏸"
        buttons.append([
            InlineKeyboardButton(
                f"{icon} {cfg['name']} / {cfg['model']}",
                callback_data=f"aicfg_promote_{cfg['id']}",
            )
        ])
    buttons.append([InlineKeyboardButton("◀ 返回AI设置", callback_data="aicfg_panel")])
    return InlineKeyboardMarkup(buttons)


async def callback_ai_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin = query.from_user
    if admin.id not in admin_user_ids:
        await query.answer("No permission", show_alert=True)
        return

    action = query.data
    try:
        if action == "aicfg_panel":
            await query.message.edit_text(get_ai_config_text(), reply_markup=get_ai_config_keyboard())
        elif action == "aicfg_add":
            context.user_data[ADMIN_AI_CONFIG_STATE] = "add"
            await query.message.reply_text(get_ai_config_help("add"))
        elif action == "aicfg_bulk":
            context.user_data[ADMIN_AI_CONFIG_STATE] = "bulk"
            await query.message.reply_text(get_ai_config_help("bulk"))
        elif action == "aicfg_select":
            await query.message.edit_text("请选择要放到首位的 AI：", reply_markup=get_ai_select_keyboard())
        elif action.startswith("aicfg_promote_"):
            config_id = action.removeprefix("aicfg_promote_")
            if promote_ai_config(config_id):
                await query.answer("已设为首位AI")
            await query.message.edit_text(get_ai_config_text(), reply_markup=get_ai_config_keyboard())
        elif action.startswith("aicfg_toggle_"):
            config_id = action.removeprefix("aicfg_toggle_")
            toggle_ai_config(config_id)
            await query.message.edit_text(get_ai_config_text(), reply_markup=get_ai_config_keyboard())
        elif action.startswith("aicfg_delete_"):
            config_id = action.removeprefix("aicfg_delete_")
            delete_ai_config(config_id)
            await query.message.edit_text(get_ai_config_text(), reply_markup=get_ai_config_keyboard())
        await query.answer()
    except ValueError as exc:
        await query.answer(str(exc), show_alert=True)


async def handle_admin_ai_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """接收管理员下一条私聊文本并保存为 AI 配置。"""
    user = update.effective_user
    if user.id not in admin_user_ids:
        return

    state = context.user_data.get(ADMIN_AI_CONFIG_STATE)
    if not state:
        return

    text = update.message.text or ""
    try:
        if state == "add":
            cfg = parse_ai_config_line(text)
            add_ai_config(
                name=cfg["name"],
                base_url=cfg["base_url"],
                api_key=cfg["api_key"],
                model=cfg["model"],
                enabled=cfg.get("enabled", True),
            )
            saved = "✅ AI 配置已新增。"
        elif state == "bulk":
            set_ai_configs(parse_ai_configs_bulk(text))
            saved = "✅ AI 配置已批量覆盖。"
        else:
            saved = "未知配置状态，已取消。"
    except ValueError as exc:
        await update.message.reply_text(f"❌ 保存失败：{exc}\n\n请按提示格式重新发送，或发送 /admin 返回后台。")
        raise ApplicationHandlerStop

    context.user_data.pop(ADMIN_AI_CONFIG_STATE, None)
    await update.message.reply_text(
        f"{saved}\n\n{get_ai_config_text()}",
        reply_markup=get_ai_config_keyboard(),
    )
    raise ApplicationHandlerStop