"""订单卡(05 §5.3):一张置顶卡看状态,通知类消息另发新消息(收款/交付带特效)。

状态 → 卡片:quoted=选付款网络;awaiting_payment=收款信息;其余=状态行。刷新=编辑原消息,编辑失败则发新卡并让旧卡去按钮。
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardMarkup

from ..config import copy
from ..experience import effects, pins, tgx, ui_registry
from ..ui import btn, kb, send_card
from ..ui.icons import render, to_plain_emoji
from . import service

logger = logging.getLogger(__name__)

NETWORK_NAMES = {"tron": "TRON (TRC20)", "bsc": "BNB Chain (BEP20)", "ethereum": "Ethereum (ERC20)", "polygon": "Polygon",
                 "solana": "Solana", "ton": "TON", "plasma": "Plasma", "aptos": "Aptos"}
PAYABLE = ("quoted", "awaiting_payment")
TOKEN = "usdt"                                       # 订单卡文案按 USDT 计价;网关里其他币种不对客户展示


def _lang(order) -> str:
    return order.lang if order.lang in ("zh", "en") else "zh"


def net_name(network: str | None) -> str:
    return NETWORK_NAMES.get((network or "").lower(), (network or "").upper())


def _expires_local(ts: float | None) -> str:
    if not ts:
        return "-"
    return (datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=8)).strftime("%m-%d %H:%M (UTC+8)")


def usdt_networks(assets: list[dict]) -> list[dict]:
    """网关当前可用且支持 USDT 的链。"""
    return [a for a in assets if any(str(t).lower() == TOKEN for t in a.get("tokens", []))]


def _status_text(order, lang: str) -> str:
    return copy("order.status", lang, id=order.id, title=html.escape(order.title or ""), state=copy(f"order.state.{order.state}", lang))


def render_card(order, assets: list[dict] | None = None, *, choose: bool = False) -> tuple[str, InlineKeyboardMarkup | None]:
    lang = _lang(order)
    title, detail = html.escape(order.title or ""), html.escape(order.detail or "")
    if order.state in PAYABLE and (choose or not order.gm_trade_id):
        text = copy("order.choose", lang, id=order.id, title=title, detail=detail, amount=order.base_amount_str or "-")
        rows, row = [], []
        for a in usdt_networks(assets or []):
            row.append(btn(a.get("display_name") or net_name(a["network"]), icon="payment", style="primary",
                           callback_data=f"pay:gm:{order.id}:{a['network']}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([btn(copy("btn.pay_help", lang), icon="chat", callback_data=f"pay:help:{order.id}")])
        return text, kb(rows)
    if order.state in PAYABLE and order.gm_trade_id:
        addr = order.pay_address or ""
        text = copy("order.quote", lang, id=order.id, title=title, detail=detail, amount=order.amount_str, network=net_name(order.network),
                    address=html.escape(addr), head=html.escape(addr[:4]), tail=html.escape(addr[-6:]), expires_local=_expires_local(order.expires_at))
        rows = [[btn(copy("btn.pay_txid", lang), icon="check", style="success", callback_data=f"pay:txid:{order.id}")]]
        if order.pay_url:
            rows.append([btn(copy("order.btn_cashier", lang), icon="payment", url=order.pay_url)])
        rows.append([btn(copy("order.btn_switch", lang), callback_data=f"pay:switch:{order.id}"),
                     btn(copy("btn.pay_help", lang), icon="chat", callback_data=f"pay:help:{order.id}")])
        return text, kb(rows)
    if order.state == "payment_review":
        return _status_text(order, lang), kb([[btn(copy("btn.pay_help", lang), icon="chat", callback_data=f"pay:help:{order.id}")]])
    return _status_text(order, lang), None


async def send(bot, order, assets: list[dict] | None = None, *, biz: str | None = None):
    """发订单卡并登记;收款信息阶段置顶。返回消息或 None。同一订单的旧卡先去按钮。"""
    text, markup = render_card(order, assets)
    msg = await send_card(bot, order.user_id, text, markup, biz)
    if not msg:
        return None
    ui_id = ui_registry.record(order.user_id, order.user_id, msg.message_id, "order", ref=order.id, biz=biz)
    if order.state in PAYABLE and order.gm_trade_id:
        await pins.pin_card(bot, ui_id)
    return msg


async def refresh(bot, order_id: str, assets: list[dict] | None = None) -> bool:
    """按订单当前状态原位更新订单卡;终态(交付/取消/退款)安排 24 小时后取消置顶。"""
    order = service.get_order(order_id)
    ui = ui_registry.latest(order.user_id, "order", ref=order_id) if order else None
    if not order or not ui:
        return False
    text, markup = render_card(order, assets)
    ok = await tgx.edit_text(bot, ui["chat_id"], ui["message_id"], render(text), ui["biz"], markup)
    if not ok:
        ok = await tgx.edit_text(bot, ui["chat_id"], ui["message_id"], to_plain_emoji(render(text)), ui["biz"], None)
    if not ok:                                         # 过期等无法编辑:去按钮并发一张新卡
        await tgx.strip_buttons(bot, ui["chat_id"], ui["message_id"], ui["biz"])
        ui_registry.set_state(ui["id"], "void")
        return bool(await send(bot, order, assets, biz=ui["biz"]))
    if order.state in ("delivered", "cancelled", "refunded", "superseded") and ui["pinned"]:
        pins.schedule_unpin(ui["id"], 24 * 3600 if order.state == "delivered" else 60)
    return True


async def notify_paid(bot, order, *, tolerance_received: str | None = None) -> None:
    """新消息 + 特效(order.paid):订单卡本身只更新状态。"""
    lang = _lang(order)
    paid = order.paid_at or datetime.now(timezone.utc)
    paid_at = (paid.replace(tzinfo=paid.tzinfo or timezone.utc) + timedelta(hours=8)).strftime("%m-%d %H:%M")
    text = copy("order.paid", lang, id=order.id, amount=order.base_amount_str or order.amount_str or "-", paid_at=paid_at,
                next_step=html.escape(order.next_step or ""))
    if tolerance_received:
        text += "\n" + copy("order.paid_tolerance_suffix", lang, received=tolerance_received)
    await effects.send(bot, order.user_id, text, None, user_id=order.user_id, moment="order.paid")


async def notify_delivered(bot, order) -> None:
    await effects.send(bot, order.user_id, copy("order.delivered", _lang(order), id=order.id), None,
                       user_id=order.user_id, moment="order.delivered")
