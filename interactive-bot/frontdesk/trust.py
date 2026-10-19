"""真伪核验:官方账号/收款地址白名单比对、冒充报告、/order 订单查询。规则驱动,零模型。"""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from db.database import SessionMaker
from db.model import Order

from ..config import cfg, copy
from ..config.defaults_flows import ENTRY_INTENTS
from .entry import extract_addresses, extract_handles

logger = logging.getLogger(__name__)

DEFAULT_BOT = "baimao_interactive_bot"


def _bot_username() -> str:
    try:
        from .. import bot_username
        return (bot_username or DEFAULT_BOT).lstrip("@")
    except Exception:
        return DEFAULT_BOT


def official_accounts() -> list[dict]:
    out = []
    for a in cfg.get("trust.official_accounts", default=[]):
        u = (a.get("username") or "").lstrip("@")
        if u == "<BOT_USERNAME>":
            u = _bot_username()
        if u:
            out.append({**a, "username": u})
    return out


def official_addresses() -> list[dict]:
    return list(cfg.get("trust.official_addresses", default=[]))


@dataclass
class Verdict:
    text: str
    unofficial: list[str] = field(default_factory=list)      # 非官方的账号/地址
    paid_reported: bool = False
    official_checked: bool = True


def _list_text(lang: str) -> str:
    rows = "\n".join(f"• @{a['username']} —— {a.get('role_zh') if lang == 'zh' else a.get('role_en')}" for a in official_accounts())
    return copy("verify.list", lang, list=rows)


def verify(text: str, lang: str) -> Verdict:
    """对文本中的每个 @用户名 / 地址逐一核对。"""
    accounts = {a["username"].lower(): a for a in official_accounts()}
    addresses = {a["address"]: a for a in official_addresses() if a.get("address")}
    parts, bad = [], []
    for h in extract_handles(text):
        acc = accounts.get(h.lower())
        if acc:
            parts.append(copy("verify.account_ok", lang, u=h, role=acc.get("role_zh") if lang == "zh" else acc.get("role_en")))
        else:
            parts.append(copy("verify.account_bad", lang, u=h))
            bad.append(f"@{h}")
    for addr in extract_addresses(text):
        off = addresses.get(addr)
        if off:
            parts.append(copy("verify.addr_ok", lang, network=off.get("network", ""), tail=addr[-6:]))
        else:
            parts.append(copy("verify.addr_bad", lang))
            bad.append(addr)
    low = (text or "").lower()
    paid = any(w in low for w in ENTRY_INTENTS["paid_words"] + ["转账", "汇款"])
    if bad or not parts:
        parts.append(_list_text(lang))
    if bad and paid:
        parts.append(copy("verify.scammed", lang))
    return Verdict("\n\n".join(parts), bad, bool(bad and paid))


def report_impersonation(user_id: int, verdict: Verdict, original: str) -> int:
    """非官方对象 → 管理群 P1 通知;同一对象每天一条。返回入队条数。"""
    from ..outbox import enqueue_admin
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    n = 0
    for obj in verdict.unofficial:
        note = (f"[[alert]] <b>P1 · 疑似冒充</b>\n客户 <a href=\"tg://user?id={user_id}\">{user_id}</a> 询问了非官方对象:<code>{html.escape(obj)}</code>"
                + ("\n客户称已转账,请联系并提醒保留凭证。" if verdict.paid_reported else "")
                + f"\n原话:{html.escape((original or '')[:200])}")
        if enqueue_admin(f"imp:{obj.lower()}:{day}", note, user_id):
            n += 1
    return n


# ---------- /order ----------
STATE_ZH = {"draft": "草稿", "quoted": "已报价", "awaiting_payment": "待付款", "payment_review": "核对付款中", "paid": "已付款",
            "fulfilling": "交付中", "delivered": "已交付", "cancelled": "已关闭", "superseded": "已被新报价替代",
            "refunded": "已退款", "disputed": "争议处理中", "expired": "已过期"}


def _amount(o) -> tuple[str, str]:
    if getattr(o, "amount_str", None):
        return o.amount_str, getattr(o, "currency", None) or "USDT"
    if o.stars:
        return str(o.stars), "Stars"
    if o.ton_nano:
        return f"{o.ton_nano / 1e9:g}", "TON"
    return "-", ""


def order_view(order_id: str, viewer_id: int, lang: str, is_admin: bool = False) -> str:
    with SessionMaker() as db:
        o = db.get(Order, order_id.upper()) or db.get(Order, order_id)
        if not o:
            return copy("order.not_found_query", lang, id=html.escape(order_id))
        if o.user_id != viewer_id and not is_admin:
            return copy("order.exists_only", lang, id=o.id)
        amount, currency = _amount(o)
        state = STATE_ZH.get(o.state, o.state) if lang == "zh" else o.state
        return copy("order.view", lang, id=o.id, title=html.escape(o.title or ""), state=state, amount=amount, currency=currency)


def recent_orders_view(user_id: int, lang: str) -> str:
    with SessionMaker() as db:
        rows = db.query(Order).filter(Order.user_id == user_id).order_by(Order.created_at.desc()).limit(5).all()
        if not rows:
            return copy("order.none", lang)
        lines = []
        for o in rows:
            amount, currency = _amount(o)
            lines.append(f"• {o.id} · {html.escape(o.title or '')} · {STATE_ZH.get(o.state, o.state) if lang == 'zh' else o.state} · {amount} {currency}")
        return "\n".join(lines)
