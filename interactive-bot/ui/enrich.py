"""全局美化层:让「所有」出站消息都带高级表情与按钮颜色,不用逐处改旧代码。

- 文本:HTML 消息里的普通 emoji(与 tgiosicons 图标同形)自动换成 <tg-emoji>;未指定 parse_mode 的纯文本先转义再换。
- 按钮:没有颜色/图标的按钮,按文字内容自动补 style(确认=绿、删除/取消=红、返回=默认、其余=蓝)与前导 emoji 对应的图标。
- 降级:被 Telegram 明确拒绝(高级表情/样式)时,先换普通 emoji 但保留颜色,再不行才全朴素;Business 消息一律不装饰。
- 安装:`install()` 对 telegram.Bot 的 send_message / edit_message_text / edit_message_reply_markup / send_photo 打补丁,幂等。
"""
from __future__ import annotations

import html
import logging
import re

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from .icons import ICONS, PACK, custom_emoji_enabled, render, suspend_custom_emoji, to_plain_emoji
from .kit import _is_style_error, plain_markup

logger = logging.getLogger(__name__)

_VS = "️"


def _key(ch: str) -> str:
    return ch.replace(_VS, "")


# 普通 emoji → (图标名, custom_emoji_id);同形多义(两个图标共用一个 emoji)的不自动替换
_EMOJI: dict[str, tuple[str, str]] = {}
_seen: dict[str, int] = {}
for _name, _ic in ICONS.items():
    _k = _key(_ic.fallback)
    _seen[_k] = _seen.get(_k, 0) + 1
    _EMOJI.setdefault(_k, (_name, _ic.id))
for _k, _n in _seen.items():
    if _n > 1:
        _EMOJI.pop(_k, None)
for _e, _i in PACK.items():                                   # 整套表情包补充 ICONS 没有的 emoji(ICONS 优先)
    _EMOJI.setdefault(_key(_e), ("pack", _i))
_EMOJI_RE = re.compile("|".join(sorted((re.escape(k) + f"{_VS}?" for k in _EMOJI), key=len, reverse=True))) if _EMOJI else None
_TAG_RE = re.compile(r"(<[^>]+>)")
_SKIP_OPEN = ("<tg-emoji", "<code", "<pre")
_SKIP_CLOSE = ("</tg-emoji", "</code", "</pre")


def enrich_html(text: str) -> str:
    """HTML 文本里的普通 emoji → <tg-emoji>(跳过标签内部、<tg-emoji>/<code>/<pre> 内容)。"""
    if not text or not _EMOJI_RE:
        return text
    out, skip = [], 0
    for part in _TAG_RE.split(text):
        if part.startswith("<") and part.endswith(">"):
            low = part.lower()
            if low.startswith(_SKIP_OPEN):
                skip += 1
            elif low.startswith(_SKIP_CLOSE):
                skip = max(0, skip - 1)
            out.append(part)
        elif skip:
            out.append(part)
        else:
            out.append(_EMOJI_RE.sub(lambda m: f'<tg-emoji emoji-id="{_EMOJI[_key(m.group(0))][1]}">{m.group(0)}</tg-emoji>', part))
    return "".join(out)


# ---------- 按钮 ----------
_DANGER = ("删除", "取消", "拒绝", "关闭", "停用", "退出", "重来", "清空", "撤销", "作废", "封禁", "❌", "🗑", "cancel", "delete", "remove", "reject", "stop")
_SUCCESS = ("确认", "保存", "发布", "发送", "立即", "开始", "通过", "批准", "解决了", "新增", "添加", "启用", "提交", "完成", "接管", "✅",
            "confirm", "save", "publish", "send", "submit", "start", "approve", "add", "enable", "done")
_NEUTRAL = ("返回", "◀", "⬅", "刷新", "🔄", "上一页", "下一页", "上一步", "back", "refresh", "prev", "next", "跳过", "skip")


def smart_style(text: str) -> str | None:
    """按按钮文字推断颜色:危险=红,确认类=绿,导航/刷新=不上色,其余=蓝。"""
    low = (text or "").lower()
    if any(k in low for k in _NEUTRAL):
        return None
    if any(k in low for k in _DANGER):
        return "danger"
    if any(k in low for k in _SUCCESS):
        return "success"
    return "primary"


def _leading_icon(text: str) -> tuple[str | None, str]:
    """文字开头的 emoji 若有对应图标 → (custom_emoji_id, 去掉 emoji 后的文字)。"""
    t = text.lstrip()
    for k, (_n, eid) in _EMOJI.items():
        if t.startswith(k):
            rest = t[len(k):].lstrip(_VS).strip()
            return (eid, rest) if rest else (None, text)
    return None, text


def decorate_markup(markup):
    """给没有 style/图标的按钮补上(已显式设置的不动)。非内联键盘原样返回。"""
    if not isinstance(markup, InlineKeyboardMarkup):
        return markup
    rows, changed = [], False
    for row in markup.inline_keyboard:
        new_row = []
        for b in row:
            kw = dict(b.api_kwargs or {})
            text = b.text
            if "icon_custom_emoji_id" not in kw:
                eid, rest = _leading_icon(text)
                if eid:
                    kw["icon_custom_emoji_id"], text = eid, rest
            if "style" not in kw:
                st = smart_style(b.text)
                if st:
                    kw["style"] = st
            if kw != (b.api_kwargs or {}) or text != b.text:
                changed = True
                new_row.append(InlineKeyboardButton(text, callback_data=b.callback_data, url=b.url,
                                                    switch_inline_query=b.switch_inline_query,
                                                    switch_inline_query_current_chat=b.switch_inline_query_current_chat,
                                                    web_app=b.web_app, api_kwargs=kw or None))
            else:
                new_row.append(b)
        rows.append(new_row)
    return InlineKeyboardMarkup(rows) if changed else markup


# ---------- 补丁 ----------
def _channel(kw: dict) -> str:
    return "business" if kw.get("business_connection_id") else "bot"


def _channel_ok(kw: dict) -> bool:
    """高级表情/按钮图标是否可用:全局开关 + (Business 时)该通道能力缓存。Business 被 Telegram 拒绝后自动关闭 24 小时。"""
    if not custom_emoji_enabled():
        return False
    if _channel(kw) == "business":
        try:
            from ..experience import capabilities
            return capabilities.ok("business", "custom_emoji")
        except Exception:
            return True
    return True


def _is_unset(v) -> bool:
    return v is None or type(v).__name__ == "DefaultValue"


def _prepare(kw: dict, text_key: str) -> dict | None:
    """返回装饰后的参数;不适用(Business/已用 entities/功能关闭)返回 None。"""
    if not _channel_ok(kw) or kw.get("entities") or kw.get("caption_entities"):
        return None
    new = dict(kw)
    changed = False
    text = new.get(text_key)
    if isinstance(text, str) and text:
        pm = new.get("parse_mode")
        if _is_unset(pm):
            enriched = enrich_html(html.escape(text))
            if enriched != html.escape(text):
                new[text_key], new["parse_mode"], changed = enriched, "HTML", True
        elif str(pm).upper() == "HTML":
            enriched = enrich_html(text)
            if enriched != text:
                new[text_key], changed = enriched, True
    rm = new.get("reply_markup")
    if isinstance(rm, InlineKeyboardMarkup):
        dec = decorate_markup(rm)
        if dec is not rm:
            new["reply_markup"], changed = dec, True
    return new if changed else None


def _render_tokens(kwargs: dict, text_key: str) -> dict:
    """兜底:任何漏渲染的 [[图标]] 占位符都不能原样出现在客户/管理员眼前。"""
    text = kwargs.get(text_key)
    if not (isinstance(text, str) and "[[" in text):
        return kwargs
    pm = kwargs.get("parse_mode")
    html_mode = not _is_unset(pm) and str(pm).upper() == "HTML"
    rich = html_mode and _channel_ok(kwargs)
    return {**kwargs, text_key: render(text, rich=rich)}


def make_wrapper(orig, text_key: str):
    async def wrapper(self, *args, **kwargs):
        if args:                                   # 位置参数调用极少见:不介入
            return await orig(self, *args, **kwargs)
        kwargs = _render_tokens(kwargs, text_key)
        new = _prepare(kwargs, text_key)
        if new is None:
            return await orig(self, **kwargs)
        try:
            return await orig(self, **new)
        except BadRequest as exc:
            if not _is_style_error(exc):
                raise
            logger.warning("decorated send rejected, degrading: %s", exc)
            if _channel(kwargs) == "business":                    # Business 不接受:只关 Business 通道 24 小时,不影响 Bot 私聊
                try:
                    from ..experience import capabilities
                    capabilities.fail("business", "custom_emoji")
                except Exception:
                    pass
            else:
                suspend_custom_emoji()
            step2 = dict(new)                      # 普通 emoji + 保留颜色
            if isinstance(step2.get(text_key), str):
                step2[text_key] = to_plain_emoji(step2[text_key])
            if "reply_markup" in step2:
                step2["reply_markup"] = plain_markup(step2["reply_markup"], keep_style=True)
            try:
                return await orig(self, **step2)
            except BadRequest as exc2:
                if not _is_style_error(exc2):
                    raise
                return await orig(self, **kwargs)  # 全朴素:调用方原参数
    wrapper._wc_wrapped = True
    return wrapper


def install() -> None:
    """幂等安装。"""
    for name, key in (("send_message", "text"), ("edit_message_text", "text"), ("send_photo", "caption"),
                      ("edit_message_reply_markup", "__none__")):
        orig = getattr(Bot, name, None)
        if orig is None or getattr(orig, "_wc_wrapped", False):
            continue
        setattr(Bot, name, make_wrapper(orig, key))
