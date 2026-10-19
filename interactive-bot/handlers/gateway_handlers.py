"""GMPay 收款流程(WP8):自定义报价 → 选网络 → 订单卡 → 自动确认 / 提交交易哈希 / 联系同事。

付款成立只来自网关(验签回调或查单状态=2);客户自述与截图永远只能进入 payment_review。
"""
from __future__ import annotations

import html
import logging
import re
import time
import uuid

from telegram import Update
from telegram.ext import ContextTypes

from whitecat_core import runtime

from ..admin import permissions
from ..commerce import order_card, service
from ..commerce.notify import admin_markup, notify_settlement
from ..commerce.providers import gmpay
from ..config import cfg, copy
from ..experience import cleanup, pins, ui_registry
from ..ui import btn, kb, send_card

logger = logging.getLogger(__name__)

_TXID_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]{64}$")
CONFIRM_ABOVE_U = 500.0


def _emit(event, user_id, **props):
    try:
        from ..analytics.events import emit
        emit(event, user_id, **props)
    except Exception:
        pass


def _lang(order) -> str:
    return order.lang if order.lang in ("zh", "en") else "zh"


async def _assets() -> list[dict]:
    """网关当前可用的 USDT 链;网关未配置/不可用返回 []。"""
    if not gmpay.configured():
        return []
    try:
        return order_card.usdt_networks(await gmpay.supported_assets())
    except gmpay.GatewayError:
        return []


async def _tell_admins(bot, order, text: str) -> None:
    from db.database import SessionMaker
    from db.model import User
    from ..global_settings import get_admin_group_id
    with SessionMaker() as db:
        u = db.query(User).filter(User.user_id == order.user_id).first()
        thread = u.message_thread_id if u else None
    await send_card(bot, get_admin_group_id(), text, admin_markup(order.id, service.get_order(order.id).state), message_thread_id=thread)


async def present_quote(bot, order) -> bool:
    """把报价发给客户:网关可用 → 选网络卡;不可用 → 告知"付款信息由同事提供"并通知管理群。
    若客户来自 Business 且尚未启动机器人私聊 → 在 Business 发送 biz.bind_bot 引导卡并在管理群提示。
    """
    assets = await _assets()
    if not assets:
        await send_card(bot, order.user_id, copy("order.gateway_down", _lang(order)), None)
        await _tell_admins(bot, order, f"[[alert]] <b>收款网关暂不可用</b>\n订单 <code>{order.id}</code> 已报价 {order.base_amount_str} USDT,请人工向客户提供付款信息并核账。")
        return False

    from ..frontdesk import profile
    if not profile.bot_started(order.user_id):
        from db.database import SessionMaker
        from db.model import BusinessContact
        with SessionMaker() as db:
            bc = db.query(BusinessContact).filter(BusinessContact.user_id == order.user_id).first()
        if bc and bc.business_connection_id:
            bot_user = None
            try:
                bot_user = await bot.get_me() if hasattr(bot, "get_me") else None
            except Exception:
                pass
            bot_uname = getattr(bot_user, "username", "") or getattr(bot, "username", "") or "WhiteCatBot"
            bind_text = copy("biz.bind_bot", _lang(order))
            bind_url = f"https://t.me/{bot_uname}?start=ord_{order.id}"
            bind_markup = kb([[btn(copy("btn.open_order", _lang(order)), url=bind_url)]])
            sent = await send_card(bot, bc.chat_id, bind_text, bind_markup, bc.business_connection_id)
            await _tell_admins(bot, order, f"[[info]] 客户尚未在官方机器人启动私聊，已在 Business 对话发送绑定引导卡(订单 <code>{order.id}</code>)。请提醒客户点击「打开订单」。")
            return bool(sent)

    return bool(await order_card.send(bot, order, assets))



async def send_custom_quote(bot, uid: int, *, title: str, detail: str, amount: str, next_step: str, created_by: int,
                            source_case_id: int | None = None):
    order = service.create_custom_quote(uid, title=title, detail=detail, amount=amount, next_step=next_step,
                                        lang=runtime.lang_for(uid), created_by=created_by, source_case_id=source_case_id)
    ok = await present_quote(bot, order)
    _emit("quote_sent", uid, order_id=order.id)
    return order, ok


# ---------- 管理员:自定义报价 ----------
async def cmd_qcustom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/qcustom <客户ID> <金额U> <标题> [| 说明 | 下一步] —— 自定义金额报价(≥ 阈值需二次确认;客服有上限)"""
    admin = update.effective_user.id
    if not permissions.can(admin, "quote_custom"):
        return
    raw = update.message.text.partition(" ")[2].strip()
    head, _, rest = raw.partition("|")
    toks = head.split(None, 2)
    detail, _, next_step = rest.partition("|")
    try:
        if len(toks) < 3 or not toks[0].lstrip("-").isdigit():
            raise ValueError("usage")
        amount = service.parse_amount(toks[1])
    except ValueError:
        await update.message.reply_text("用法: /qcustom <客户ID> <金额U> <标题> [| 说明 | 下一步]\n"
                                        "例: /qcustom 123456 288 自动回复机器人 | 含 3 个功能 | 负责同事今天 18:00 前确认开工时间")
        return
    limit = permissions.quote_limit(admin)
    if limit is not None and float(amount) > limit:
        await update.message.reply_text(f"你的角色单笔报价上限为 {limit:g} U,请联系经理或业主。")
        return
    fields = dict(uid=int(toks[0]), title=toks[2].strip(), detail=detail.strip(), amount=format(amount, "f"), next_step=next_step.strip())
    if float(amount) >= float(cfg.get("quote.confirm_above_u", default=CONFIRM_ABOVE_U)):
        token = uuid.uuid4().hex[:8]
        context.bot_data.setdefault("pending_quotes", {})[token] = (admin, fields)
        await update.message.reply_text(
            f"请确认报价:客户 {fields['uid']} · {html.escape(fields['title'])} · <b>{fields['amount']} USDT</b>", parse_mode="HTML",
            reply_markup=kb([[btn("确认发送", icon="check", style="success", callback_data=f"qc:ok:{token}"),
                              btn("取消", icon="cross", callback_data=f"qc:no:{token}")]]))
        return
    await _do_send_quote(update.message.reply_text, context.bot, admin, fields)


async def _do_send_quote(reply, bot, admin: int, fields: dict) -> None:
    try:
        order, ok = await send_custom_quote(bot, fields["uid"], title=fields["title"], detail=fields["detail"], amount=fields["amount"],
                                            next_step=fields["next_step"], created_by=admin)
    except ValueError:
        await reply("金额无效。")
        return
    await reply(f"已发送报价 {order.id}({fields['amount']} USDT)" if ok else f"报价 {order.id} 已创建,但未能送达或收款网关暂不可用,请人工跟进。")


async def callback_quote_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, action, token = q.data.split(":", 2)
    pending = context.bot_data.get("pending_quotes", {})
    entry = pending.get(token)
    if not entry or entry[0] != q.from_user.id:
        await q.answer("已失效或不是你的报价", show_alert=True)
        return
    pending.pop(token, None)
    await q.answer()
    if action != "ok":
        await q.edit_message_text("已取消。")
        return
    await q.edit_message_text("发送中…")
    await _do_send_quote(q.message.reply_text, context.bot, q.from_user.id, entry[1])


# ---------- 客户:选网络 / 提交哈希 / 求助 ----------
async def callback_gateway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = (q.data or "").split(":")
    if len(parts) < 3:
        await q.answer()
        return
    method, oid = parts[1], parts[2]
    order = service.get_order(oid)
    if not order:
        await q.answer("Order not found", show_alert=True)
        return
    lang = _lang(order)
    if q.from_user.id != order.user_id:
        await q.answer(copy("order.not_yours", lang), show_alert=True)
        return
    live = order.state in ("quoted", "awaiting_payment") and not (order.expires_at and time.time() > order.expires_at)
    if not (live or (order.state == "payment_review" and method in ("help", "txid"))):
        await q.answer()
        await send_card(context.bot, order.user_id, copy("order.expired", lang))
        return
    await q.answer()
    if method == "help":
        from ..frontdesk.service import handoff
        handoff(order.user_id, "support_escalation:payment", summary=f"订单 {order.id} 付款疑问")
        await _tell_admins(context.bot, order, f"[[alert]] <b>客户对付款有疑问</b>\n订单 <code>{order.id}</code>|{html.escape(order.title)}")
        await send_card(context.bot, order.user_id, copy("order.lookup_down", lang))
    elif method == "txid":
        context.user_data["await_txid"] = order.id
        await send_card(context.bot, order.user_id, copy("order.ask_txid", lang))
    elif method == "switch":
        text, markup = order_card.render_card(order, await _assets(), choose=True)
        await send_card(context.bot, order.user_id, text, markup)
    elif method == "gm" and len(parts) > 3:
        await _choose_network(context.bot, order, parts[3])


async def _choose_network(bot, order, network: str) -> None:
    lang = _lang(order)
    try:
        order = await service.attach_gateway(order.id, network, order_card.TOKEN)
    except (gmpay.GatewayError, KeyError):
        await send_card(bot, order.user_id, copy("order.gateway_down", lang))
        await _tell_admins(bot, order, f"[[alert]] <b>网关下单失败</b>\n订单 <code>{order.id}</code>,请人工提供付款信息。")
        return
    _emit("pay_clicked", order.user_id, method="gmpay")
    if await order_card.refresh(bot, order.id):
        ui = ui_registry.latest(order.user_id, "order", ref=order.id)
        if ui:
            await pins.pin_card(bot, ui["id"])
    else:
        await order_card.send(bot, order)


def tx_link(txid: str, network: str | None) -> str:
    return f"https://tronscan.org/#/transaction/{txid}" if (network or "").lower() == "tron" else txid


async def maybe_txid(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """客户消息是交易哈希(或正在等待交易哈希)→ 处理并返回 True;否则 False 交还给后续流程。"""
    uid = update.effective_user.id
    waiting = context.user_data.get("await_txid")
    t = (text or "").strip()
    is_hash = bool(_TXID_RE.match(t))
    orders = [o for o in service.open_orders(uid) if o.gm_trade_id]
    if not (waiting or (is_hash and orders)):
        return False
    order = service.get_order(waiting) if waiting else orders[0]
    if not order or order.user_id != uid:
        context.user_data.pop("await_txid", None)
        return False
    lang = _lang(order)
    if not is_hash:
        await send_card(context.bot, uid, copy("order.bad_txid", lang))
        return True
    context.user_data.pop("await_txid", None)
    txid = t.lower().removeprefix("0x")
    if not service.claim_event(f"txid:{txid}", order.id, "txid", "submitted", ""):
        if service.event_order(f"txid:{txid}") == order.id:          # 同一订单重复提交:温和回应
            await send_card(context.bot, uid, copy("order.lookup_down", lang))
            return True
        await send_card(context.bot, uid, copy("order.txid_taken", lang))
        await _tell_admins(context.bot, order, f"[[alert]] <b>交易哈希重复提交</b>\n订单 <code>{order.id}</code>\n{tx_link(txid, order.network)}")
        return True
    tmp = await send_card(context.bot, uid, copy("order.checking", lang))
    try:
        paid = await gmpay.check_status(order.gm_trade_id) == gmpay.STATUS_PAID
    except gmpay.GatewayError:
        paid = False
    if tmp:
        await cleanup.delete_temp(context.bot, ui_registry.record(uid, uid, tmp.message_id, "checking", ref=order.id))
    if paid:
        verdict, oid = service.settle_gmpay(order.gm_trade_id, txid=txid, source="txid")
        if oid and verdict not in ("duplicate", "not_mine"):
            await notify_settlement(context.bot, oid, verdict)
        return True
    service.transition(order.id, "payment_review", actor="customer_txid")
    await order_card.refresh(context.bot, order.id)
    await send_card(context.bot, uid, copy("order.lookup_down", lang))
    await _tell_admins(context.bot, order, f"[[alert]] <b>客户提交了交易哈希,网关尚未确认</b>\n订单 <code>{order.id}</code>|{order.base_amount_str} USDT|{order.network}\n"
                                           f"{tx_link(txid, order.network)}\n请核对链上到账后点“核实无误,确认收款”。")
    return True
