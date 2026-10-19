"""先答卡:价目、总览、官方入口、身份直答、各业务线说明。规则渲染,零模型。

价目数字只来自知识库(products[].plans);缺失时退回官方入口卡并记录 warning。
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal

from telegram import InlineKeyboardMarkup

from ..config import copy
from ..config.defaults_flows import PLAN_NAME_EN
from ..rag.retriever import _load_knowledge
from ..ui import btn, kb

logger = logging.getLogger(__name__)

BUY_BOT_URL = "https://t.me/oxbaimaobot"

_DUR_EN = {"永久": "forever", "终身": "forever"}


def _product(pid: str) -> dict:
    return next((p for p in _load_knowledge().get("products", []) if p.get("id") == pid), {})


def _official() -> dict:
    return _load_knowledge().get("official", {})


def _duration(raw: str, lang: str) -> str:
    if lang == "zh":
        return raw
    if raw in _DUR_EN:
        return _DUR_EN[raw]
    m = re.match(r"(\d+)\s*天", raw)
    return f"{m.group(1)} days" if m else raw


def _plan_name(plan: dict, lang: str) -> str:
    if lang == "zh":
        return plan["name"]
    return plan.get("name_en") or PLAN_NAME_EN.get(plan["name"]) or plan["name"]


def _plan_row(plan: dict, lang: str) -> str:
    unit = "U"
    parts = [f"{_plan_name(plan, lang)} {plan['price']} {unit}", _duration(plan.get("duration", ""), lang)]
    seats = plan.get("seats", "")
    m = re.match(r"(\d+)", seats)
    if m:                                                    # 单设备价格不赘述,多设备席位才标注
        n = m.group(1)
        parts.append(f"{n} 台设备" if lang == "zh" else f"{n} devices")
    row = "• " + " · ".join(x for x in parts if x)
    if "限" in plan.get("note", "") and lang == "zh":
        row += "(每台设备限 1 次)"
    elif "限" in plan.get("note", "") and lang == "en":
        row += " (once per device)"
    return row


def software_plans() -> list[dict]:
    return _product("software").get("plans", [])


def _buy_row(lang: str, extra_rows=()) -> list:
    rows = [[btn(copy("btn.official_buy", lang), style="primary", callback_data="fd:cta:official_buy")],
            [btn(copy("btn.help_choose", lang), callback_data="fd:go:software_pick")],
            [btn(copy("btn.other_services", lang), callback_data="fd:menu:more")]]
    return rows + list(extra_rows)


def price_software(lang: str) -> tuple[str, InlineKeyboardMarkup]:
    plans = software_plans()
    if not plans:
        logger.warning("knowledge base has no software plans; falling back to official_buy card")
        return official_buy(lang)
    rows = "\n".join(_plan_row(p, lang) for p in plans)
    return copy("card.price_software", lang, rows=rows), kb(_buy_row(lang))


def price_overview(lang: str) -> tuple[str, InlineKeyboardMarkup]:
    cb = lambda k, line: btn(copy(k, lang), callback_data=f"fd:go:{line}")
    markup = kb([
        [cb("btn.ov.software", "software"), cb("btn.ov.custom", "bot_custom")],
        [cb("btn.ov.network", "network"), cb("btn.ov.ai", "ai_account")],
        [cb("btn.ov.other", "other_biz")],
    ])
    return copy("card.price_overview", lang), markup


def official_buy(lang: str) -> tuple[str, InlineKeyboardMarkup]:
    off = _official()
    rows = [[btn(copy("btn.open_buy_bot", lang), style="primary", url=off.get("purchase_bot_url") or BUY_BOT_URL)]]
    if off.get("releases"):
        rows.append([btn(copy("btn.download", lang), url=off["releases"])])
    rows.append([btn(copy("btn.have_question", lang), callback_data="fd:go:software_ai")])
    return copy("card.official_buy", lang), kb(rows)


def is_bot(lang: str) -> tuple[str, None]:
    return copy("card.is_bot", lang), None


def custom_intro(lang: str) -> tuple[str, None]:
    return copy("card.custom_intro", lang), None


def network_intro(lang: str) -> tuple[str, None]:
    return copy("card.network_intro", lang), None


def ai_intro(lang: str) -> tuple[str, None]:
    return copy("card.ai_intro", lang), None


LINE_INTRO = {"bot_custom": custom_intro, "network": network_intro, "ai_account": ai_intro}

HORIZONS = ("trial", "1m", "3m", "1y", "multi")


def software_pick_question(lang: str) -> tuple[str, InlineKeyboardMarkup]:
    rows = [[btn(copy(f"opt.software.horizon.{h}", lang), callback_data=f"fd:pick:{h}")] for h in HORIZONS]
    return copy("q.software.horizon", lang), kb(rows)


def _monthly_cost(plan: dict) -> Decimal | None:
    m = re.match(r"(\d+)", plan.get("duration", ""))
    if not m:
        return None
    days = Decimal(m.group(1))
    return Decimal(plan["price"]) * Decimal(30) / days


def software_recommend(horizon: str, devices: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    plans = software_plans()
    if len(plans) < 5 or horizon not in HORIZONS:
        return official_buy(lang)
    starter, monthly, quarterly, annual, lifetime = plans[:5]
    pick = {"trial": starter, "1m": monthly, "3m": quarterly, "1y": annual, "multi": lifetime}[horizon]
    price = Decimal(pick["price"])
    ann_month = (Decimal(annual["price"]) / 12).to_integral_value(rounding="ROUND_CEILING")
    if horizon == "trial":
        reason = ("新设备本来就有免费试用,先用免费的,不够再买体验卡。" if lang == "zh"
                  else "New devices already include a free trial — start with that, and add the starter pass if you need more time.")
    elif horizon == "1m":
        reason = (f"按 1 个月的使用时间,月度最划算;如果确定会长期用,年度平均每月不到 {ann_month} U。" if lang == "zh"
                  else f"For about a month, Monthly is the best value; if you'll stay longer, Annual works out under {ann_month} U a month.")
    elif horizon == "3m":
        reason = ("按 3 个月使用,季度比连续买三个月度便宜。" if lang == "zh"
                  else "For about three months, Quarterly costs less than buying Monthly three times.")
    elif horizon == "1y":
        reason = ("按一年以上使用,年度版每月成本最低。" if lang == "zh"
                  else "For a year or more, Annual has the lowest monthly cost.")
    else:
        reason = ("长期使用并且有多台设备,终身版一次买断,含 3 台设备。" if lang == "zh"
                  else "For long-term use on several devices, Lifetime is a one-time purchase and covers 3 devices.")
    compare = ""
    if devices and devices >= 2:
        total = Decimal(annual["price"]) * devices
        if lang == "zh":
            compare = f"{devices} 台设备:{devices} × 年度 = {total} U/年;终身版 {lifetime['price']} U 含 3 台设备。"
        else:
            compare = f"{devices} devices: {devices} × Annual = {total} U/year; Lifetime is {lifetime['price']} U and covers 3 devices."
    text = copy("card.software_recommend", lang, plan=_plan_name(pick, lang), price=price.normalize() if price == price.to_integral() else price,
                duration=_duration(pick.get("duration", ""), lang), reason=reason, compare=compare).rstrip()
    markup = kb([[btn(copy("btn.official_buy", lang), style="primary", callback_data="fd:cta:official_buy")],
                 [btn(copy("btn.ask_details", lang), callback_data="fd:go:software_ai")]])
    return text, markup
