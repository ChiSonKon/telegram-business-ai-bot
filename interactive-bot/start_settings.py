"""/start 欢迎语与按钮配置。

这里集中处理管理后台可配置的欢迎消息、透明按钮（InlineKeyboard）和
底部按钮（ReplyKeyboard）。按钮颜色按 Telegram 新版能力预留，当前
python-telegram-bot 21.3 尚未提供显式参数，因此通过 api_kwargs 透传，
并在发送端保留无样式降级路径。
"""
from __future__ import annotations

import json
from typing import Any

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from db.database import SessionMaker, engine
from db.model import AppSetting

from . import welcome_message as env_welcome_message


SETTING_WELCOME = "start.welcome_message"
SETTING_INLINE_BUTTONS = "start.inline_buttons"
SETTING_BOTTOM_BUTTONS = "start.bottom_buttons"

COLOR_LABELS = {
    "transparent": "透明",
    "blue": "蓝色",
    "green": "绿色",
    "red": "红色",
}
VALID_COLORS = set(COLOR_LABELS)
INLINE_ACTIONS = {"human", "url", "callback"}
BOTTOM_ACTIONS = {"text", "human"}

DEFAULT_INLINE_BUTTONS = [
    {
        "text": "👩‍💻 呼叫客服/转人工",
        "action": "human",
        "value": "",
        "color": "blue",
        "emoji_id": "",
        "row": 1,
    },
    {
        "text": "🌐 访问官网",
        "action": "url",
        "value": "https://example.com/",
        "color": "transparent",
        "emoji_id": "",
        "row": 2,
    },
]
DEFAULT_BOTTOM_BUTTONS: list[dict[str, Any]] = []


def ensure_settings_table() -> None:
    """确保旧数据库升级后也存在运行时配置表。"""
    AppSetting.__table__.create(bind=engine, checkfirst=True)


def _session():
    return SessionMaker()


def _get_setting(key: str, default: str) -> str:
    ensure_settings_table()
    db = _session()
    try:
        setting = db.query(AppSetting).filter(AppSetting.key == key).first()
        return setting.value if setting else default
    finally:
        db.close()


def _set_setting(key: str, value: str) -> None:
    ensure_settings_table()
    db = _session()
    try:
        setting = db.query(AppSetting).filter(AppSetting.key == key).first()
        if setting:
            setting.value = value
        else:
            setting = AppSetting(key=key, value=value)
            db.add(setting)
        db.commit()
    finally:
        db.close()


def get_welcome_message() -> str:
    return _get_setting(SETTING_WELCOME, env_welcome_message or "欢迎使用本机器人")


def set_welcome_message(text: str) -> None:
    _set_setting(SETTING_WELCOME, text.strip() or "欢迎使用本机器人")


def _loads_buttons(raw: str, default: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [_normalize_button(item) for item in data if isinstance(item, dict)]
    except Exception:
        pass
    return default


def get_inline_buttons() -> list[dict[str, Any]]:
    raw = _get_setting(SETTING_INLINE_BUTTONS, json.dumps(DEFAULT_INLINE_BUTTONS, ensure_ascii=False))
    return _loads_buttons(raw, DEFAULT_INLINE_BUTTONS)


def get_bottom_buttons() -> list[dict[str, Any]]:
    raw = _get_setting(SETTING_BOTTOM_BUTTONS, json.dumps(DEFAULT_BOTTOM_BUTTONS, ensure_ascii=False))
    return _loads_buttons(raw, DEFAULT_BOTTOM_BUTTONS)


def set_inline_buttons(buttons: list[dict[str, Any]]) -> None:
    _set_setting(SETTING_INLINE_BUTTONS, json.dumps(buttons, ensure_ascii=False))


def set_bottom_buttons(buttons: list[dict[str, Any]]) -> None:
    _set_setting(SETTING_BOTTOM_BUTTONS, json.dumps(buttons, ensure_ascii=False))


def _normalize_color(color: str | None) -> str:
    color = (color or "transparent").strip().lower()
    aliases = {
        "透明": "transparent",
        "蓝色": "blue",
        "蓝": "blue",
        "绿色": "green",
        "绿": "green",
        "红色": "red",
        "红": "red",
    }
    color = aliases.get(color, color)
    return color if color in VALID_COLORS else "transparent"


def _normalize_button(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": str(item.get("text", "按钮")).strip() or "按钮",
        "action": str(item.get("action", "text")).strip().lower(),
        "value": str(item.get("value", "")).strip(),
        "color": _normalize_color(str(item.get("color", "transparent"))),
        "emoji_id": str(item.get("emoji_id", "")).strip(),
        "row": int(item.get("row", 1) or 1),
    }


def _style_kwargs(color: str, emoji_id: str, include_style: bool) -> dict[str, Any]:
    if not include_style:
        return {}
    
    color_map = {
        "blue": "primary",
        "green": "success",
        "red": "danger",
    }
    
    api_kwargs = {}
    if color in color_map:
        api_kwargs["style"] = color_map[color]
    
    if emoji_id:
        api_kwargs["icon_custom_emoji_id"] = emoji_id
        
    return {"api_kwargs": api_kwargs} if api_kwargs else {}


def _matrix(buttons: list[Any], rows: list[int]) -> list[list[Any]]:
    grouped: dict[int, list[Any]] = {}
    for btn, row in zip(buttons, rows):
        grouped.setdefault(max(1, row), []).append(btn)
    return [grouped[row] for row in sorted(grouped)]


def build_start_inline_markup(user_id: int, include_style: bool = True) -> InlineKeyboardMarkup | None:
    buttons = []
    rows = []
    for cfg in get_inline_buttons():
        action = cfg.get("action") if cfg.get("action") in INLINE_ACTIONS else "human"
        kwargs = _style_kwargs(cfg.get("color", "transparent"), cfg.get("emoji_id", ""), include_style)
        if action == "url":
            value = cfg.get("value", "")
            if not value:
                continue
            button = InlineKeyboardButton(cfg["text"], url=value, **kwargs)
        elif action == "callback":
            value = cfg.get("value", "") or f"start_custom_{user_id}"
            button = InlineKeyboardButton(cfg["text"], callback_data=value[:64], **kwargs)
        else:
            button = InlineKeyboardButton(cfg["text"], callback_data=f"call_human_{user_id}", **kwargs)
        buttons.append(button)
        rows.append(int(cfg.get("row", 1) or 1))
    return InlineKeyboardMarkup(_matrix(buttons, rows)) if buttons else None


def build_start_bottom_markup(include_style: bool = True) -> ReplyKeyboardMarkup | None:
    buttons = []
    rows = []
    for cfg in get_bottom_buttons():
        kwargs = _style_kwargs(cfg.get("color", "transparent"), cfg.get("emoji_id", ""), include_style)
        buttons.append(KeyboardButton(cfg["text"], **kwargs))
        rows.append(int(cfg.get("row", 1) or 1))
    if not buttons:
        return None
    return ReplyKeyboardMarkup(_matrix(buttons, rows), resize_keyboard=True, is_persistent=True)


def get_bottom_button_action(text: str) -> dict[str, Any] | None:
    normalized = (text or "").strip()
    for cfg in get_bottom_buttons():
        if cfg.get("text") == normalized:
            return cfg
    return None


def parse_button_config(message: telegram.Message, area: str) -> list[dict[str, Any]]:
    """解析后台输入的按钮配置 (PostBot 语法)。"""
    text = message.text or ""
    if text.strip().lower() in {"clear", "empty", "none", "清空", "无"}:
        return []

    # 提取自定义表情
    emoji_map = {}
    if message.entities:
        for ent, ent_text in message.parse_entities([telegram.MessageEntity.CUSTOM_EMOJI]).items():
            if ent.custom_emoji_id:
                emoji_map[ent_text] = ent.custom_emoji_id

    allowed = INLINE_ACTIONS if area == "inline" else BOTTOM_ACTIONS
    default_action = "human" if area == "inline" else "text"
    buttons: list[dict[str, Any]] = []
    
    for row_idx, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
            
        parts = [p.strip() for p in line.split("|")]
        for part in parts:
            if not part:
                continue
            
            sub_parts = part.replace("—", "-").split("-")
            sub_parts = [sp.strip() for sp in sub_parts]
            
            label = sub_parts[0]
            action = default_action
            value = ""
            color = "transparent"
            emoji_id = ""
            
            # 匹配自定义表情并从文字中移除（避免 icon + 文字双重显示）
            for em_text, em_id in emoji_map.items():
                if em_text in label:
                    emoji_id = em_id
                    label = label.replace(em_text, "").strip()
                    break
            
            if len(sub_parts) > 1:
                val_str = sub_parts[1].lower()
                val_raw = sub_parts[1]
                if val_str == "human" or not val_str:
                    action = "human"
                elif val_str.startswith(("http://", "https://", "tg://", "@")):
                    action = "url"
                    value = val_raw
                    if value.startswith("@"):
                        value = f"https://t.me/{value[1:]}"
                elif val_str.startswith("callback:"):
                    action = "callback"
                    value = val_raw[9:]
                elif val_str in {"transparent", "blue", "green", "red", "透明", "蓝色", "绿色", "红色"}:
                    color = _normalize_color(val_str)
                else:
                    action = "url" if "://" in val_str else ("callback" if area == "inline" else "text")
                    value = val_raw
                    
            if len(sub_parts) > 2:
                color = _normalize_color(sub_parts[2])
                
            if area == "inline" and action == "url" and not value.startswith(("http://", "https://", "tg://")):
                raise ValueError(f"URL 必须以 http://、https:// 或 tg:// 开头：{value}")
                
            if action not in allowed:
                raise ValueError(f"动作不支持：{action}，允许：{', '.join(sorted(allowed))}")
                
            buttons.append({
                "text": label,
                "action": action,
                "value": value,
                "color": color,
                "emoji_id": emoji_id,
                "row": row_idx
            })
            
    return buttons


def format_buttons(buttons: list[dict[str, Any]]) -> str:
    if not buttons:
        return "未设置"
    lines = []
    grouped = {}
    for btn in buttons:
        grouped.setdefault(btn.get("row", 1), []).append(btn)
        
    for row in sorted(grouped.keys()):
        row_str = []
        for btn in grouped[row]:
            color = COLOR_LABELS.get(btn.get("color", "transparent"), "透明")
            emoji_flag = "+" if btn.get("emoji_id") else ""
            txt = f"{emoji_flag}{btn.get('text')} ({btn.get('action')}{': ' + btn.get('value') if btn.get('value') else ''} - {color})"
            row_str.append(txt)
        lines.append(f"第{row}行: " + " | ".join(row_str))
        
    return "\n".join(lines)


def build_call_human_markup(user_id: int) -> InlineKeyboardMarkup:
    """构建包含"呼叫人工"按钮的 InlineKeyboardMarkup（自动继承后台配置的颜色和Emoji）。"""
    human_cfg = None
    for cfg in get_inline_buttons():
        if cfg.get("action") == "human":
            human_cfg = cfg
            break

    if human_cfg:
        text = human_cfg.get("text", "👩‍💻 呼叫客服/转人工")
        kwargs = _style_kwargs(
            human_cfg.get("color", "transparent"),
            human_cfg.get("emoji_id", ""),
            include_style=True,
        )
    else:
        text = "👩‍💻 呼叫白猫/转人工"
        kwargs = {}

    button = InlineKeyboardButton(
        text,
        callback_data=f"call_human_{user_id}",
        **kwargs,
    )
    return InlineKeyboardMarkup([[button]])


import html as pyhtml

def get_start_config_text() -> str:
    return (
        "<b>👋 /start 欢迎页设置</b>\n"
        "==============================\n\n"
        f"<b>欢迎消息：</b>\n{get_welcome_message()}\n\n"
        f"<b>透明按钮（消息下方 Inline）：</b>\n{pyhtml.escape(format_buttons(get_inline_buttons()))}\n\n"
        f"<b>底部按钮（输入框 Reply Keyboard）：</b>\n{pyhtml.escape(format_buttons(get_bottom_buttons()))}\n\n"
        "<i>颜色支持：透明 transparent、蓝色 blue、绿色 green、红色 red。</i>"
    )


def get_button_config_help(area: str) -> str:
    if area == "inline":
        return (
            "👇 请发送透明按钮配置:\n\n"
            "• 换行 = 新的一行按钮\n"
            "• 一行内多个按钮使用 | 分隔\n"
            "• 颜色写在末尾: green, blue, 或者是 red\n"
            "• 支持直接在按钮文字中插入 Premium 自定义表情！\n\n"
            "模板示例:\n"
            "👩‍💻 呼叫客服/转人工 - human - blue\n"
            "🌐 访问官网 - https://example.com/ | 💡 帮助 - callback:help\n\n"
            "发送“清空”可删除所有透明按钮。"
        )
    return (
        "👇 请发送底部按钮配置:\n\n"
        "• 换行 = 新的一行按钮\n"
        "• 一行内多个按钮使用 | 分隔\n"
        "• 颜色写在末尾: green, blue, 或者是 red\n"
        "• 支持直接在按钮文字中插入 Premium 自定义表情！\n\n"
        "模板示例:\n"
        "👩‍💻 呼叫人工 - human - green\n"
        "💳 支付方式 - text - blue\n\n"
        "发送“清空”可删除所有底部按钮。"
    )