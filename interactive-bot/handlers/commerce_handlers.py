"""成交与支付处理器:管理员报价、客户选择支付方式、Stars 发票、管理员核账/交付。

原则:金额、地址、链接只来自目录配置与订单行;付款成立只来自 Telegram 的 successful_payment 或链上事件。
"""
from __future__ import annotations

import html
import json
import logging
import time

from telegram import LabeledPrice, Update
from telegram.ext import ContextTypes

from whitecat_core import runtime
from whitecat_core.payments import ton

from .. import admin_user_ids, bot_username
from ..admin import permissions
from ..commerce import order_card, service
from ..commerce.notify import admin_markup, notify_settlement
from ..ui import btn, edit_card, kb, send_card

logger = logging.getLogger(__name__)

T = {
    "zh": {
        "card": "[[package]] <b>{title}</b>\n\n报价有效期 {mins} 分钟。请选择付款方式，付款后会自动确认，无需发送截图。",
        "stars": "用 Telegram Stars 付款（{n}⭐）",
        "ton": "用 TON 钱包付款（{n} TON）",
        "expired": "[[clock]] 这个报价已过期，请联系我们重新获取报价。",
        "ton_card": "[[payment]] <b>TON 付款</b>\n订单 <code>{id}</code>\n金额 <code>{amount}</code> TON\n收款地址 <code>{addr}</code>\n备注（必填） <code>{memo}</code>\n\n点击下方按钮会在钱包里自动带入金额与备注。到账后自动确认。",
        "open_wallet": "打开钱包付款",
        "invoice_fail": "[[alert]] 暂时无法发起 Stars 付款，请先点击机器人对话框中的“开始/Start”，或联系我们。",
        "not_yours": "这不是您的订单",
        "denied": "仅管理员可用",
    },
    "en": {
        "card": "[[package]] <b>{title}</b>\n\nThis quote is valid for {mins} minutes. Choose how to pay — it's confirmed automatically, no screenshot needed.",
        "stars": "Pay with Telegram Stars ({n}⭐)",
        "ton": "Pay with TON wallet ({n} TON)",
        "expired": "[[clock]] This quote has expired. Please ask us for a fresh one.",
        "ton_card": "[[payment]] <b>Pay with TON</b>\nOrder <code>{id}</code>\nAmount <code>{amount}</code> TON\nAddress <code>{addr}</code>\nMemo (required) <code>{memo}</code>\n\nThe button below opens your wallet with the amount and memo filled in. We confirm automatically on arrival.",
        "open_wallet": "Open wallet to pay",
        "invoice_fail": "[[alert]] We couldn't start the Stars payment. Please tap Start in the bot chat first, or contact us.",
        "not_yours": "This isn't your order",
        "denied": "Admins only",
    },
}


def _emit(event, user_id, **props):
    try:
        from ..analytics.events import emit
        emit(event, user_id, **props)
    except Exception:
        pass


def _publish_quick(admin_id: int, key: str, value, note: str) -> None:
    """旧命令已写入旧存储;同步登记配置版本(历史/回滚/审计)。失败不影响命令本身。"""
    try:
        from ..config import store
        store.quick_publish(admin_id, key, value, note)
    except Exception as exc:
        logger.warning("quick publish %s failed: %s", key, type(exc).__name__)


def _lang(order) -> str:
    return order.lang if order.lang in T else "zh"


def _is_admin(user_id: int) -> bool:
    return permissions.is_admin(user_id)


def _can(user_id: int, cap: str) -> bool:
    return permissions.can(user_id, cap)


# ---------- 管理员命令 ----------
async def cmd_setcatalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setcatalog {"sku1":{"title_zh":"月度","title_en":"Monthly","stars":1500,"ton":6.5}}"""
    if not _can(update.effective_user.id, "config_edit"):
        return
    raw = update.message.text.partition(" ")[2].strip()
    if not raw:
        await update.message.reply_text("当前目录:\n" + json.dumps(service.get_catalog(), ensure_ascii=False, indent=2)
                                        + "\n\n用法: /setcatalog <JSON>  (字段: title_zh,title_en,stars(整数),ton(数字))")
        return
    try:
        data = json.loads(raw)
        service.set_catalog(data)
        _publish_quick(update.effective_user.id, "catalog", data, "/setcatalog")
    except Exception as exc:
        await update.message.reply_text(f"目录无效: {exc}")
        return
    await update.message.reply_text("目录已保存。")


async def cmd_setton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setton <收款地址> [toncenter_api_key]"""
    if not _can(update.effective_user.id, "config_edit"):
        return
    parts = update.message.text.split()[1:]
    if not parts:
        cfg = service.get_ton_config()
        await update.message.reply_text(f"当前收款地址: {cfg.get('address', '未设置')}\n用法: /setton <地址> [api_key]")
        return
    from ..config import store
    cfg = service.get_ton_config()
    if len(parts) > 1:
        cfg["api_key"] = parts[1]
        service.set_ton_config(cfg)
    try:
        store.save_draft(update.effective_user.id, "payments.ton.address", parts[0])
    except permissions.PermissionDenied:
        await update.message.reply_text(permissions.deny_text(update.effective_user.id))
        return
    await update.message.reply_text("收款地址属于高危配置:已存为草稿。请打开 /cfg → 收款与信任 → TON 收款地址 发布(需输入确认短语,发布后延迟生效,期间业主可撤销)。")


async def send_quote(bot, uid: int, sku: str):
    """创建报价并把带付款按钮的决策卡发给客户。返回 (order, 是否送达);目录里没有该商品抛 KeyError。"""
    lang = runtime.lang_for(uid)
    order = service.create_quote(uid, sku, lang)
    if order.base_amount_str and order.currency == "USDT":        # USDT 标价商品:选网络 → 订单卡(GMPay)
        from .gateway_handlers import present_quote
        ok = await present_quote(bot, order)
        _emit("quote_sent", uid, order_id=order.id)
        return order, ok
    tx = T[lang]
    rows = []
    if order.stars:
        rows.append([btn(tx["stars"].format(n=order.stars), icon="star", style="primary", callback_data=f"pay:stars:{order.id}")])
    if order.ton_nano:
        rows.append([btn(tx["ton"].format(n=order.ton_nano / ton.NANO), icon="payment", style="primary", callback_data=f"pay:ton:{order.id}")])
    msg = await send_card(bot, uid, tx["card"].format(title=html.escape(order.title), mins=service.QUOTE_MINUTES), kb(rows))
    _emit("quote_sent", uid, order_id=order.id)
    return order, bool(msg)


async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/quote <客户ID> <sku> —— 向客户发送一屏决策卡(含支付方式按钮)"""
    if not _can(update.effective_user.id, "quote_catalog"):
        return
    args = update.message.text.split()[1:]
    if len(args) != 2 or not args[0].lstrip("-").isdigit():
        await update.message.reply_text("用法: /quote <客户ID> <sku>")
        return
    try:
        order, ok = await send_quote(context.bot, int(args[0]), args[1])
    except KeyError:
        await update.message.reply_text(f"目录里没有 {args[1]}。先用 /setcatalog 设置。")
        return
    await update.message.reply_text(f"已发送报价 {order.id}" if ok else f"报价 {order.id} 已创建，但发送失败（客户可能未启动机器人）。")


# ---------- 客户支付 ----------
async def callback_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        _, method, oid = q.data.split(":", 2)
    except ValueError:
        await q.answer()
        return
    order = service.get_order(oid)
    if not order:
        await q.answer("Order not found", show_alert=True)
        return
    tx = T[_lang(order)]
    if q.from_user.id != order.user_id:
        await q.answer(tx["not_yours"], show_alert=True)
        return
    if order.state not in ("quoted", "awaiting_payment") or time.time() > (order.expires_at or 0):
        await q.answer()
        await send_card(context.bot, order.user_id, tx["expired"])
        return
    await q.answer()
    _emit("pay_clicked", order.user_id, method=method)
    service.transition(oid, "awaiting_payment", actor="customer")     # quoted -> awaiting;已在 awaiting 则无操作
    if method == "stars" and order.stars:
        try:
            await context.bot.send_invoice(
                chat_id=order.user_id, title=order.title[:32], description=order.title[:255], payload=order.id,
                provider_token="", currency="XTR", prices=[LabeledPrice(order.title[:32], order.stars)],
            )
        except Exception as exc:
            logger.warning("send_invoice failed: %s", type(exc).__name__)
            await send_card(context.bot, order.user_id, tx["invoice_fail"])
    elif method == "ton" and order.ton_nano:
        cfg = service.get_ton_config()
        intent = ton.PaymentIntent(order.id, cfg["address"], order.ton_nano, "TON", order.ton_memo, order.expires_at)
        text = tx["ton_card"].format(id=order.id, amount=f"{order.ton_nano / ton.NANO:g}", addr=html.escape(cfg["address"]),
                                     memo=html.escape(order.ton_memo))
        await send_card(context.bot, order.user_id, text, kb([[btn(tx["open_wallet"], icon="payment", style="success", url=ton.build_wallet_link(intent))]]))


async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.pre_checkout_query
    order = service.get_order(q.invoice_payload)
    ok = bool(order and order.stars and q.currency == "XTR" and q.total_amount == order.stars
              and q.from_user.id == order.user_id and order.state in ("quoted", "awaiting_payment")
              and time.time() <= (order.expires_at or 0))
    await q.answer(ok=ok, error_message=None if ok else "This order is no longer payable / 订单已失效，请重新获取报价")


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p = update.message.successful_payment
    verdict = service.settle_stars(p.invoice_payload, p.telegram_payment_charge_id, p.total_amount, p.currency)
    if verdict in ("duplicate", "unknown_order"):
        logger.warning("stars settle: %s payload=%s", verdict, p.invoice_payload)
        return
    await notify_settlement(context.bot, p.invoice_payload, verdict)


# ---------- 管理员核账/交付 ----------
async def callback_order_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        await q.answer(T["zh"]["denied"], show_alert=True)
        return
    try:
        _, action, oid = q.data.split(":", 2)
    except ValueError:
        await q.answer()
        return
    moves = {"confirm": ("paid", True), "fulfill": ("fulfilling", False), "deliver": ("delivered", False), "cancel": ("cancelled", False)}
    to, verified = moves.get(action, (None, False))
    if not to:
        await q.answer()
        return
    if not _can(q.from_user.id, "payment_ops" if to in ("paid", "cancelled") else "deliver"):
        await q.answer(permissions.deny_text(q.from_user.id), show_alert=True)
        return
    actor = f"admin:{q.from_user.id}"
    if not service.transition(oid, to, actor=actor, verified=verified):
        await q.answer("状态已变化，请刷新", show_alert=True)
        return
    order = service.get_order(oid)
    await q.answer("OK")
    if order.state == "delivered":
        _emit("delivered", order.user_id)
    await edit_card(q, f"[[check]] 订单 <code>{oid}</code> → <b>{order.state}</b>（{html.escape(q.from_user.full_name)}）",
                    admin_markup(oid, order.state))
    if order.gm_trade_id:                                  # GMPay 订单:订单卡更新 + 新消息(带特效)
        await order_card.refresh(context.bot, oid)
        if order.state == "delivered":
            await order_card.notify_delivered(context.bot, order)
        elif order.state == "paid":
            await order_card.notify_paid(context.bot, order)
    elif order.state == "delivered":
        lang = _lang(order)
        await send_card(context.bot, order.user_id, "[[check]] 您的订单已交付完成，如有问题请直接在这里留言。" if lang == "zh"
                        else "[[check]] Your order has been delivered. Message us here if anything looks off.")


async def cmd_setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setprice <输入价$/百万token> <输出价$/百万token> [每日预算$] —— 配置后启用按美元的全局预算"""
    if not _can(update.effective_user.id, "config_edit"):
        return
    from ..global_settings import update_global_config
    try:
        a = [float(x) for x in update.message.text.split()[1:]]
        if len(a) not in (2, 3) or any(x < 0 for x in a):
            raise ValueError
    except ValueError:
        await update.message.reply_text("用法: /setprice <输入价> <输出价> [每日预算USD]（单位:美元/百万token）")
        return
    upd = {"llm_price_in_per_m": a[0], "llm_price_out_per_m": a[1]}
    if len(a) == 3:
        upd["global_daily_budget_usd"] = a[2]
    update_global_config(upd)
    for k, v in upd.items():
        _publish_quick(update.effective_user.id, "budget." + k, v, "/setprice")
    await update.message.reply_text("已保存。之后按美元核算每日预算；耗尽后 AI 暂停，客户留言转同事。")


async def cmd_resetprompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/resetprompt —— 用仓库中的 sales_system_prompt.txt 覆盖后台已存的运营提示词(一次性迁移用)"""
    if not _can(update.effective_user.id, "config_edit"):
        return
    from .. import prompt_settings
    with open(prompt_settings._PROMPT_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()
    prompt_settings.set_system_prompt(content)
    _publish_quick(update.effective_user.id, "ai.operator_prompt", content, "/resetprompt")
    await update.message.reply_text(f"运营提示词已重置为仓库版本（{len(content)} 字）。")


async def cmd_setlines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setlines <JSON数组> —— 管理业务线注册表;不带参数查看当前配置"""
    if not _can(update.effective_user.id, "config_edit"):
        return
    from ..frontdesk import lines
    raw = update.message.text.partition(" ")[2].strip()
    if not raw:
        await update.message.reply_text(json.dumps(lines.get_lines(), ensure_ascii=False, indent=1)[:3800])
        return
    try:
        data = json.loads(raw)
        lines.set_lines(data)
        _publish_quick(update.effective_user.id, "lines", data, "/setlines")
    except Exception as exc:
        await update.message.reply_text(f"业务线无效: {exc}")
        return
    await update.message.reply_text("业务线已保存，立即生效。")
