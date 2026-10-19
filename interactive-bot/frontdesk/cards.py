"""前台卡片与按钮(中英双语)。WP1 起欢迎卡为"先答"结构(03 §3.2);旧四选一保留为 welcome_markup_legacy,用于兼容旧卡片。"""
from telegram import InlineKeyboardMarkup

from ..config import copy
from ..ui import btn, kb
from .texts import T, t

WELCOME = copy("welcome", "zh")


def welcome_text(lang: str = "zh") -> str:
    return copy("welcome", lang)


def welcome_markup(user_id: int = 0, lang: str = "zh", source: str | None = None) -> InlineKeyboardMarkup:
    from .router import welcome_markup as _wm
    return _wm(source, lang)


def welcome_markup_legacy(user_id: int, lang: str = "zh") -> InlineKeyboardMarkup:
    cd = lambda role: f"frontdesk_intent:{role}:{user_id}"
    return kb([
        [btn(t("btn_prospect", lang), icon="package", style="primary", callback_data=cd("prospect"))],
        [btn(t("btn_support", lang), icon="user", style="primary", callback_data=cd("support"))],
        [btn(t("btn_partner", lang), icon="briefcase", style="primary", callback_data=cd("partner"))],
        [btn(t("btn_visitor", lang), icon="book", callback_data=cd("visitor"))],
    ])


def intent_text(role: str, lang: str = "zh") -> str:
    return t(f"intent_{role}" if f"intent_{role}" in T["zh"] else "intent_default", lang)
