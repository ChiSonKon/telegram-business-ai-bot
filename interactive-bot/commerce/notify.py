"""支付结算后的双向通知:客户(按其语言)+ 管理群话题(带可操作按钮)。"""
from __future__ import annotations

import html
import logging

from db.database import SessionMaker
from db.model import User
from telegram import InlineKeyboardMarkup

from ..global_settings import get_admin_group_id
from ..ui import btn, kb, send_card
from . import service

logger = logging.getLogger(__name__)

CUSTOMER = {
    "zh": {
        "exact": "[[check]] <b>已收到您的付款</b>\n订单 <code>{id}</code>：{title}\n正在为您安排开通/交付，完成后会在这里通知您，无需再发送截图。",
        "review": "[[clock]] <b>付款已收到，正在核对</b>\n订单 <code>{id}</code> 的金额或时间需要人工确认，已转交同事处理，核对完成后会在这里通知您。",
        "receipt": "[[clock]] <b>付款凭证已收到，正在等待核对。</b>\n请补充订单号或交易流水号，便于匹配。核验完成后会通知您后续交付安排。",
    },
    "en": {
        "exact": "[[check]] <b>Payment received</b>\nOrder <code>{id}</code>: {title}\nWe're arranging delivery now and will confirm here — no screenshot needed.",
        "review": "[[clock]] <b>Payment received — being verified</b>\nThe amount or timing on order <code>{id}</code> needs a teammate's confirmation. We'll update you here once done.",
        "receipt": "[[clock]] <b>Payment proof received — awaiting verification.</b>\nPlease add your order number or transaction ID to help us match it. We'll update you once verified.",
    },
}
_VERDICT_ZH = {
    "underpaid": "少付", "overpaid": "多付", "wrong_asset": "币种/资产不符", "expired_late": "报价过期后到账",
    "orphan_payment": "订单已作废/取消,但收到了付款", "unknown_order": "找不到订单", "receipt": "客户发来付款凭证（截图不是付款证明，请核对后再确认）",
}


def admin_markup(order_id: str, state: str) -> InlineKeyboardMarkup:
    if state == "paid":
        return kb([[btn("开始交付", icon="package", style="primary", callback_data=f"ord:fulfill:{order_id}")]])
    if state == "fulfilling":
        return kb([[btn("标记已交付", icon="check", style="success", callback_data=f"ord:deliver:{order_id}")]])
    if state == "payment_review":
        return kb([[btn("核实无误，确认收款", icon="check", style="success", callback_data=f"ord:confirm:{order_id}")],
                   [btn("取消订单", icon="alert", style="danger", callback_data=f"ord:cancel:{order_id}")]])
    return kb([])


async def notify_settlement(bot, order_id: str, verdict: str, *, tell_customer: bool = True) -> None:
    order = service.get_order(order_id)
    if not order:
        return
    lang = order.lang if order.lang in CUSTOMER else "zh"
    key = "exact" if verdict == "exact" else "review"
    if tell_customer and order.gm_trade_id:                       # GMPay 订单:订单卡原位更新 + 新消息(带特效)
        from . import order_card
        from ..config import copy
        await order_card.refresh(bot, order.id)
        fresh = service.get_order(order.id)
        if fresh.state == "paid":
            await order_card.notify_paid(bot, fresh)
        elif verdict == "expired_late":
            await send_card(bot, order.user_id, copy("order.expired_late", lang))
        else:
            await send_card(bot, order.user_id, CUSTOMER[lang]["review"].format(id=order.id, title=html.escape(order.title)))
    elif tell_customer:
        await send_card(bot, order.user_id, CUSTOMER[lang][key].format(id=order.id, title=html.escape(order.title)))
    with SessionMaker() as db:
        u = db.query(User).filter(User.user_id == order.user_id).first()
        thread = u.message_thread_id if u else None
    label = "✅ 已验证收款" if verdict in ("exact", "tolerance") else f"⚠️ 需人工核对：{_VERDICT_ZH.get(verdict, verdict)}"
    text = (f"[[bell]] <b>{label}</b>\n订单 <code>{order.id}</code>｜{html.escape(order.title)}\n"
            f"客户 <code>{order.user_id}</code>｜状态 <code>{service.get_order(order.id).state}</code>")
    await send_card(bot, get_admin_group_id(), text, admin_markup(order.id, service.get_order(order.id).state),
                    message_thread_id=thread)


async def register_receipt(bot, user_id: int) -> bool:
    """客户发来付款截图/凭证:订单只进入 payment_review 并通知管理员,绝不视为已付款。无待付订单时返回 False。"""
    for order in service.open_orders(user_id):
        if order.state == "awaiting_payment":
            service.transition(order.id, "payment_review", actor="customer_receipt")
        lang = order.lang if order.lang in CUSTOMER else "zh"
        await send_card(bot, user_id, CUSTOMER[lang]["receipt"])
        await notify_settlement(bot, order.id, "receipt", tell_customer=False)
        return True
    return False
