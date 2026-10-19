"""
Telegram iOS Icons 自定义表情注册表（https://t.me/addemoji/tgiosicons）

每个图标同时保存 custom_emoji_id 与同义普通 emoji：
- 卡片正文用 [[name]] 占位，渲染为 <tg-emoji>（需 Bot 所有者有 Premium）；
- 按钮用 icon_custom_emoji_id 显示在文字前；
- 发送失败或 CUSTOM_EMOJI=FALSE 时自动降级为普通 emoji。
"""
from __future__ import annotations

import os
import time
import re
from typing import NamedTuple


class Icon(NamedTuple):
    id: str
    fallback: str


ICONS: dict[str, Icon] = {
    # 状态 / 反馈
    "check": Icon("5774022692642492953", "✅"),
    "cross": Icon("6030757850274336631", "❌"),
    "info": Icon("6028435952299413210", "ℹ️"),
    "question": Icon("6030848053177486888", "❓"),
    "alert": Icon("6030563507299160824", "❗️"),
    "ban": Icon("5938215362473496448", "🚫"),
    "sparkles": Icon("5778226250149532337", "✨"),
    "star": Icon("6028338546736107668", "⭐️"),
    "glow": Icon("5805331990618053402", "🌟"),
    "party": Icon("6041731551845159060", "🎉"),
    "new": Icon("5895669571058142797", "🆕"),
    "heart": Icon("6037533152593842454", "❤️"),
    "like": Icon("6041720006973067267", "👍"),
    "wave": Icon("6041921818896372382", "👋"),
    "smile": Icon("5774034804450267485", "🙂"),
    "think": Icon("6043960760130868895", "🤔"),
    "sleep": Icon("5983401171501454028", "💤"),
    "point_up": Icon("5886676966102274844", "👆"),
    # 导航
    "back": Icon("5960671702059848143", "⬅️"),
    "next": Icon("5895383238473421210", "➡️"),
    "undo": Icon("5895507195524550741", "↩️"),
    "refresh": Icon("5769248574499983619", "🔄"),
    "repeat": Icon("6030657343744644592", "🔁"),
    "up": Icon("6028205772117118673", "⬆️"),
    "down": Icon("6037157012242960559", "⬇️"),
    "boost": Icon("5938437708635443119", "⏫"),
    "play": Icon("5773626993010546707", "▶️"),
    "plus": Icon("6032924188828767321", "➕"),
    "edit": Icon("6039779802741739617", "✏️"),
    "note": Icon("5920046907782074235", "📝"),
    "search": Icon("6032850693348399258", "🔎"),
    "pin": Icon("6043896193887506430", "📌"),
    "location": Icon("6030399199030284183", "📍"),
    "compass": Icon("6030687898141987254", "🧭"),
    "link": Icon("6028171274939797252", "🔗"),
    "globe": Icon("5776233299424843260", "🌐"),
    "home": Icon("6042137469204303531", "🏠"),
    "trash": Icon("6039522349517115015", "🗑"),
    "outbox": Icon("6039573425268201570", "📤"),
    "inbox": Icon("6041730074376410123", "📥"),
    "folder": Icon("6037475557082403885", "📁"),
    "tag": Icon("5888620056551625531", "🏷"),
    "radio_on": Icon("5776428312414917091", "⭕️"),
    "radio_off": Icon("5884332803016891855", "⚪️"),
    # 人 / 沟通
    "user": Icon("6032994772321309200", "👤"),
    "users": Icon("6032609071373226027", "👥"),
    "speak": Icon("6032653721853234759", "🗣"),
    "chat": Icon("6030784887093464891", "💬"),
    "phone": Icon("6039605143601680423", "📞"),
    "mic": Icon("6030467682283818947", "🎙"),
    "megaphone": Icon("6021418126061605425", "📢"),
    "broadcast": Icon("6039450962865688331", "📣"),
    "bell": Icon("6039486778597970865", "🔔"),
    "bell_off": Icon("6039569594157371705", "🔕"),
    "robot": Icon("6030400221232501136", "🤖"),
    "brain": Icon("5864019342873598613", "🧠"),
    "crown": Icon("5805553606635559688", "👑"),
    "student": Icon("5938195768832692153", "🎓"),
    "briefcase": Icon("5938492039971737551", "💼"),
    # 业务
    "target": Icon("6032949275732742941", "🎯"),
    "bulb": Icon("5891120964468480450", "💡"),
    "chart": Icon("5936143551854285132", "📊"),
    "trend": Icon("5938539885907415367", "📈"),
    "diamond": Icon("5776023601941582822", "💎"),
    "money": Icon("5778421276024509124", "💰"),
    "coin": Icon("5778613750688911681", "🪙"),
    "rich": Icon("5902206159095339799", "🤑"),
    "gift": Icon("5773677501825945508", "🎁"),
    "bolt": Icon("5920515922505765329", "⚡️"),
    "shield": Icon("6030537007350944596", "🛡"),
    "lock": Icon("6037249452824072506", "🔒"),
    "unlock": Icon("6037496202990194718", "🔓"),
    "payment": Icon("5258368777350816286", "🪙"),
    "book": Icon("5258328383183396223", "📖"),
    "package": Icon("5778672437122045013", "📦"),
    "puzzle": Icon("5837069325034331827", "🧩"),
    "shop": Icon("5920332557466997677", "🏪"),
    "wifi": Icon("6048723247501938454", "🛜"),
    "gear": Icon("5904258298764334001", "⚙️"),
    "clock": Icon("5850317551090800862", "⏰"),
    "timer": Icon("6037268453759389862", "⏲️"),
    "hourglass": Icon("5891211339170326418", "⌛️"),
    "calendar": Icon("5890937706803894250", "📅"),
    "photo": Icon("6030466823290360017", "🖼"),
    "camera": Icon("6030506650522096180", "📷"),
    # 数字
    "n1": Icon("5794164805065514131", "1️⃣"),
    "n2": Icon("5794085322400733645", "2️⃣"),
    "n3": Icon("5794280000383358988", "3️⃣"),
    "n4": Icon("5794241397217304511", "4️⃣"),
    "n5": Icon("5793985348446984682", "5️⃣"),
    "n6": Icon("5794324702402976226", "6️⃣"),
    "n7": Icon("5793942849745591465", "7️⃣"),
    "n8": Icon("5793926687783655907", "8️⃣"),
    "n9": Icon("5793979472931723221", "9️⃣"),
}

def _load_pack() -> dict[str, str]:
    """整套 tgiosicons(getStickerSet 导出):emoji → custom_emoji_id。覆盖 ICONS 之外的绝大多数常用 emoji。"""
    import json
    import pathlib
    try:
        return json.loads((pathlib.Path(__file__).with_name("tgios_pack.json")).read_text(encoding="utf-8"))
    except Exception:
        return {}


PACK: dict[str, str] = _load_pack()
_FALLBACK_BY_ID = {icon.id: icon.fallback for icon in ICONS.values()}
for _e, _i in PACK.items():
    _FALLBACK_BY_ID.setdefault(_i, _e)
_TOKEN_RE = re.compile(r"\[\[([a-z0-9_]+)\]\]")
_TG_EMOJI_RE = re.compile(r'<tg-emoji emoji-id="(\d+)">(.*?)</tg-emoji>', re.S)

# 运行时开关：CUSTOM_EMOJI=FALSE 可全局关闭自定义表情
_custom_enabled = os.getenv("CUSTOM_EMOJI", "TRUE").upper() != "FALSE"


_suspended_until = 0.0


def custom_emoji_enabled() -> bool:
    return _custom_enabled and time.time() >= _suspended_until


def suspend_custom_emoji(seconds: float = 600.0) -> None:
    """自定义表情被 Telegram 拒绝时临时停用,冷却后自动恢复(账号条件可能变化)。"""
    global _suspended_until
    _suspended_until = time.time() + seconds


def set_custom_emoji_enabled(enabled: bool) -> None:
    global _custom_enabled
    _custom_enabled = enabled


def icon_id(name: str | None) -> str | None:
    icon = ICONS.get(name or "")
    return icon.id if icon else None


def fallback(name: str | None) -> str:
    icon = ICONS.get(name or "")
    return icon.fallback if icon else ""


def fallback_for_id(emoji_id: str) -> str:
    return _FALLBACK_BY_ID.get(emoji_id, "")


def number_icon(n: int) -> str:
    return f"n{n}" if 1 <= n <= 9 else "pin"


def render(template: str, rich: bool | None = None) -> str:
    """把 [[name]] 渲染为 <tg-emoji>（rich）或普通 emoji。模板中的动态内容需调用方自行 HTML 转义。"""
    use_rich = custom_emoji_enabled() if rich is None else rich

    def _sub(m: re.Match) -> str:
        icon = ICONS.get(m.group(1))
        if not icon:
            return ""
        if use_rich:
            return f'<tg-emoji emoji-id="{icon.id}">{icon.fallback}</tg-emoji>'
        return icon.fallback

    return _TOKEN_RE.sub(_sub, template)


def strip_tokens(text: str) -> str:
    """去掉 [[name]] 占位符（用于 AI 提示词、管理群纯文本等）。"""
    return _TOKEN_RE.sub("", text).strip()


def to_plain_emoji(html_text: str) -> str:
    """已渲染文本中的 <tg-emoji> 降级为普通 emoji。"""
    return _TG_EMOJI_RE.sub(lambda m: m.group(2), html_text)
