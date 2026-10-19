"""
卡片 / 按钮工具：彩色按钮 + tgiosicons 图标 + 失败自动降级。

按钮配色约定：
- primary（蓝）：主操作、继续、导航
- success（绿）：确认、提交、成交、已选
- danger （红）：转人工、重来、接管等需要注意的操作
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from .icons import suspend_custom_emoji, custom_emoji_enabled, fallback, fallback_for_id, icon_id, render, to_plain_emoji

logger = logging.getLogger(__name__)

import re

STYLES = {"primary", "success", "danger"}

_LEADING_EMOJI_RE = re.compile(
    r"^[\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50\u2b55\ufe00-\ufe0f\s]+"
)


def strip_leading_emoji(text: str) -> str:
    cleaned = _LEADING_EMOJI_RE.sub("", text).strip()
    return cleaned if cleaned else text


def btn(
    text: str,
    *,
    icon: str | None = None,
    style: str | None = None,
    callback_data: str | None = None,
    url: str | None = None,
    emoji_id: str | None = None,
) -> InlineKeyboardButton:
    """构建带颜色与 tgiosicons 图标的内联按钮。"""
    api_kwargs: dict[str, Any] = {}
    if style in STYLES:
        api_kwargs["style"] = style
    eid = emoji_id or icon_id(icon)
    if eid and custom_emoji_enabled():
        api_kwargs["icon_custom_emoji_id"] = str(eid)
        text = strip_leading_emoji(text)
    elif icon:
        if not text.startswith(fallback(icon)):
            text = f"{fallback(icon)} {text}"
    kwargs: dict[str, Any] = {"api_kwargs": api_kwargs} if api_kwargs else {}
    if url:
        return InlineKeyboardButton(text, url=url, **kwargs)
    return InlineKeyboardButton(text, callback_data=callback_data or "ignore", **kwargs)


def kb(rows: Iterable[Iterable[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([list(r) for r in rows if r])


def plain_markup(markup: InlineKeyboardMarkup | None, keep_style: bool = False) -> InlineKeyboardMarkup | None:
    """去掉自定义图标（图标转为文字前缀）与颜色，用于降级重发。keep_style=True 时保留按钮颜色(只丢高级表情)。"""
    if not isinstance(markup, InlineKeyboardMarkup):
        return markup
    rows = []
    for row in markup.inline_keyboard:
        new_row = []
        for b in row:
            text = b.text
            eid = (b.api_kwargs or {}).get("icon_custom_emoji_id")
            if eid:
                fb = fallback_for_id(eid)
                if fb and not text.startswith(fb):
                    text = f"{fb} {text}".strip()
            style = (b.api_kwargs or {}).get("style") if keep_style else None
            new_row.append(
                InlineKeyboardButton(
                    text,
                    callback_data=b.callback_data,
                    url=b.url,
                    switch_inline_query=b.switch_inline_query,
                    switch_inline_query_current_chat=b.switch_inline_query_current_chat,
                    **({"api_kwargs": {"style": style}} if style else {}),
                )
            )
        rows.append(new_row)
    return InlineKeyboardMarkup(rows)


def _is_style_error(e: Exception) -> bool:
    """只有明确指向高级表情/按钮样式的报错才算"样式失败"。普通 HTML 解析错误(can't parse entities)不是,
    否则一次无关的格式错误会让全局高级表情被关闭 10 分钟。"""
    msg = str(e).lower()
    return any(k in msg for k in ("emoji", "style", "icon"))


async def send_card(
    bot,
    chat_id: int,
    template: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    business_connection_id: str | None = None,
    **extra: Any,
):
    """发送 HTML 卡片：template 中的 [[icon]] 自动渲染；样式失败降级，Business 失败回退普通私聊。"""
    text = render(template)
    base: dict[str, Any] = {"chat_id": chat_id, "parse_mode": "HTML", **extra}
    if business_connection_id:
        base["business_connection_id"] = business_connection_id

    # 三档降级:完整(高级表情+颜色)→ 普通表情+保留颜色 → 全朴素
    attempts = [(text, reply_markup), (to_plain_emoji(text), plain_markup(reply_markup, keep_style=True)),
                (to_plain_emoji(text), plain_markup(reply_markup))]
    last_err: Exception | None = None
    for i, (t, m) in enumerate(attempts):
        try:
            return await bot.send_message(text=t, reply_markup=m, **base)
        except BadRequest as e:
            last_err = e
            if i < len(attempts) - 1 and _is_style_error(e):
                logger.warning(f"send_card styled send failed, degrade: {e}")
                if i == 0:
                    if business_connection_id:
                        try:
                            from ..experience import capabilities
                            capabilities.fail("business", "custom_emoji")
                        except Exception:
                            pass
                    else:
                        suspend_custom_emoji()
                continue
            break
        except Exception as e:  # 网络等错误不降级
            last_err = e
            break

    logger.warning(f"send_card failed (chat={chat_id}): {last_err}")
    return None


async def edit_card(
    query,
    template: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    business_connection_id: str | None = None,
):
    """在原消息上原位更新卡片，避免刷屏；兼容 Business 消息，失败则补发新消息。"""
    text = render(template)
    extra: dict[str, Any] = {}
    if business_connection_id:
        extra["api_kwargs"] = {"business_connection_id": business_connection_id}

    for t, m in ((text, reply_markup), (to_plain_emoji(text), plain_markup(reply_markup, keep_style=True)),
                 (to_plain_emoji(text), plain_markup(reply_markup))):
        try:
            await query.edit_message_text(text=t, reply_markup=m, parse_mode="HTML", **extra)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            logger.warning(f"edit_card failed: {e}")
        except Exception as e:
            logger.warning(f"edit_card failed: {e}")
            break

    chat_id = query.message.chat_id if query.message else query.from_user.id
    await send_card(query.get_bot(), chat_id, template, reply_markup, business_connection_id)


async def replace_card(
    query,
    template: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    business_connection_id: str | None = None,
):
    """点击按钮后"弹出新消息 + 删掉旧卡片"(Telegram 删除消息时有消散动画),比原位编辑更有仪式感。

    只用于 Bot 私聊;Business、新卡发送失败、旧卡无法删除(>48h)时分别回退为原位编辑/去按钮,绝不丢失内容。
    """
    from ..config import cfg
    old = query.message
    if business_connection_id or old is None or getattr(old, "message_id", None) is None or not cfg.get("experience.button_replace.enabled", default=True):
        return await edit_card(query, template, reply_markup, business_connection_id)
    chat_id = old.chat_id
    sent = await send_card(query.get_bot(), chat_id, template, reply_markup)
    if sent is None:                                     # 新卡没发出去:不删旧卡,退回原位编辑
        return await edit_card(query, template, reply_markup, business_connection_id)
    try:                                                 # 新卡继承旧卡的登记(进度卡/已知问题卡等),后续"收起/清理"才找得到它
        from ..experience import ui_registry
        prev = ui_registry.by_message(chat_id, old.message_id)
        if prev and getattr(sent, "message_id", None):
            ui_registry.record(prev["user_id"], chat_id, sent.message_id, prev["kind"], ref=prev["ref"], biz=prev["biz"])
            ui_registry.set_state(prev["id"], "deleted")
    except Exception as exc:
        logger.info("ui registry carry-over failed: %s", type(exc).__name__)
    try:
        await query.get_bot().delete_message(chat_id=chat_id, message_id=old.message_id)
    except Exception as exc:                             # 超过 48 小时等:至少去掉旧卡上的按钮,避免重复点击
        logger.info("delete old card failed: %s", type(exc).__name__)
        try:
            await query.get_bot().edit_message_reply_markup(chat_id=chat_id, message_id=old.message_id, reply_markup=None)
        except Exception:
            pass
    return sent


def progress_bar(current: int, total: int) -> str:
    total = max(total, 1)
    current = min(max(current, 0), total)
    return "▰" * current + "▱" * (total - current) + f"  {current}/{total}"
