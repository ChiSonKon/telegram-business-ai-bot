"""订单持久化服务:目录、报价、条件状态转移、支付事实认领。所有金额与地址只来自配置,绝不来自模型。"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from db.database import SessionMaker
from db.model import AppSetting, Order, PaymentEvent
from whitecat_core.payments import ton
from .providers import gmpay
from whitecat_core.payments.orders import IllegalTransition, Order as FsmOrder, TRANSITIONS, VERIFIED_ONLY

CATALOG_KEY = "commerce.catalog"
TON_KEY = "commerce.ton"
QUOTE_MINUTES = 30


# ---------- 配置 ----------
def _get(key: str, default):
    with SessionMaker() as db:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        try:
            return json.loads(row.value) if row and row.value else default
        except ValueError:
            return default


def _set(key: str, value) -> None:
    with SessionMaker() as db:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        text = json.dumps(value, ensure_ascii=False)
        if row:
            row.value = text
        else:
            db.add(AppSetting(key=key, value=text))
        db.commit()


def get_catalog() -> dict:
    """{sku: {"title_zh","title_en","stars"(int,可空),"ton"(float,可空)}}"""
    from ..config import cfg
    ov = cfg.get("catalog")
    return ov if ov else _get(CATALOG_KEY, {})


def set_catalog(catalog: dict) -> None:
    for sku, item in catalog.items():
        if not isinstance(item, dict) or not (item.get("stars") or item.get("ton") or item.get("usdt")):
            raise ValueError(f"{sku}: 至少需要 usdt / stars / ton 价格之一")
        if item.get("usdt") is not None:
            try:
                parse_amount(str(item["usdt"]))
            except ValueError:
                raise ValueError(f"{sku}: usdt 价格必须是大于 0.01、最多两位小数的数字") from None
        if item.get("category") is not None and not isinstance(item["category"], str):
            raise ValueError(f"{sku}: category 必须是业务线编号(文字)")
        if item.get("stars") is not None and (not isinstance(item["stars"], int) or item["stars"] <= 0):
            raise ValueError(f"{sku}: stars 必须是正整数")
        if item.get("ton") is not None and not (isinstance(item["ton"], (int, float)) and item["ton"] > 0):
            raise ValueError(f"{sku}: ton 必须是正数")
    _set(CATALOG_KEY, catalog)


def get_ton_config() -> dict:
    """{"address","api_base","api_key","secret"} ;address 缺失则链上支付不可用。"""
    out = dict(_get(TON_KEY, {}))
    from ..config import cfg
    addr = cfg.get("payments.ton.address")            # 配置中心发布的收款地址(高危,延迟生效)优先于旧存储
    if addr:
        out["address"] = addr
    return out


def set_ton_config(cfg: dict) -> None:
    cfg = dict(cfg)
    cfg.setdefault("api_base", "https://toncenter.com")
    cfg.setdefault("secret", uuid.uuid4().hex)
    _set(TON_KEY, cfg)


def _secret() -> bytes:
    cfg = get_ton_config()
    if "secret" not in cfg:
        set_ton_config(cfg)
        cfg = get_ton_config()
    return cfg["secret"].encode()


# ---------- 订单 ----------
def create_quote(user_id: int, sku: str, lang: str = "zh") -> Order:
    item = get_catalog().get(sku)
    if not item:
        raise KeyError(sku)
    if item.get("usdt"):
        return _create_usdt_catalog_quote(user_id, sku, item, lang)
    oid = uuid.uuid4().hex[:10].upper()
    ton_cfg = get_ton_config()
    ton_on = bool(item.get("ton") and ton_cfg.get("address"))
    row = Order(
        id=oid, user_id=user_id, sku=sku, state="quoted", lang=lang,
        title=item.get("title_en" if lang == "en" else "title_zh") or item.get("title_zh") or sku,
        stars=item.get("stars"), ton_nano=int(round(item["ton"] * ton.NANO)) if ton_on else None,
        ton_memo=ton.make_memo(oid, _secret()) if ton_on else None,
        expires_at=time.time() + QUOTE_MINUTES * 60,
    )
    with SessionMaker() as db:
        # 同一客户同一 SKU 的旧报价作废(改单 = 新报价);其他 SKU/自定义订单不受影响
        for old in db.query(Order).filter(Order.user_id == user_id, Order.sku == sku, Order.state.in_(("quoted", "awaiting_payment"))):
            old.state = "superseded"
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
    return row


def _create_usdt_catalog_quote(user_id: int, sku: str, item: dict, lang: str) -> Order:
    """目录里按 USDT 标价的商品:建待选网络的报价(收款走 GMPay);同客户同 SKU 的旧报价作废。"""
    from ..config import cfg
    amt = parse_amount(str(item["usdt"]))
    hours = float(cfg.get("payments.quote_valid_hours", default=24))
    title = item.get("title_en" if lang == "en" else "title_zh") or item.get("title_zh") or sku
    row = Order(id=uuid.uuid4().hex[:10].upper(), user_id=user_id, sku=sku, state="quoted", lang=lang, title=title[:200],
                detail=(item.get("detail") or "")[:1000], next_step=(item.get("next_step") or "")[:500], currency="USDT",
                base_amount_str=format(amt, "f"), expires_at=time.time() + hours * 3600)
    with SessionMaker() as db:
        for old in db.query(Order).filter(Order.user_id == user_id, Order.sku == sku, Order.state.in_(("quoted", "awaiting_payment"))):
            old.state = "superseded"
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
    return row


def get_order(order_id: str) -> Order | None:
    with SessionMaker() as db:
        row = db.get(Order, order_id)
        if row:
            db.expunge(row)
        return row


def open_orders(user_id: int) -> list[Order]:
    with SessionMaker() as db:
        rows = db.query(Order).filter(Order.user_id == user_id, Order.state.in_(("awaiting_payment", "payment_review"))).all()
        db.expunge_all()
        return rows


def transition(order_id: str, to: str, *, actor: str, verified: bool = False) -> bool:
    """条件更新:仅当订单仍处于允许转移的状态时成功。并发下只有一个调用者能完成同一转移。"""
    if to in VERIFIED_ONLY and not verified:
        raise IllegalTransition(f"{to} requires verified payment fact")
    allowed_from = [s for s, nxt in TRANSITIONS.items() if to in nxt]
    with SessionMaker() as db:
        res = db.execute(update(Order).where(Order.id == order_id, Order.state.in_(allowed_from)).values(state=to))
        db.commit()
        return res.rowcount == 1


def event_order(event_key: str) -> str | None:
    with SessionMaker() as db:
        row = db.query(PaymentEvent).filter(PaymentEvent.event_key == event_key).first()
        return row.order_id if row else None


def claim_event(event_key: str, order_id: str, kind: str, verdict: str, amount_raw: str) -> bool:
    """认领一笔支付事实;已被认领(任何订单)返回 False。"""
    with SessionMaker() as db:
        db.add(PaymentEvent(event_key=event_key, order_id=order_id, kind=kind, verdict=verdict, amount_raw=amount_raw))
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False


# ---------- 支付结算 ----------
def _apply(order: Order, verdict: str, actor: str) -> str:
    """把已认领的支付事实落到订单。无法转移(订单已作废/取消/终态)时返回 orphan_payment,由调用方通知管理员人工处理。"""
    if order.state == "quoted":
        transition(order.id, "awaiting_payment", actor=actor)
    if verdict == "exact":
        ok = transition(order.id, "paid", actor=actor, verified=True)
    else:
        ok = transition(order.id, "payment_review", actor=actor)
    if not ok and get_order(order.id).state not in ("paid", "payment_review"):
        return "orphan_payment"
    try:
        from ..analytics.events import emit
        if verdict == "exact":
            emit("payment_verified", order.user_id, verdict="exact", method=actor.split(":")[0][:16])
        else:
            emit("payment_review", order.user_id, verdict=verdict[:24])
    except Exception:
        pass
    return verdict


def settle_stars(order_id: str, charge_id: str, amount: int, currency: str) -> str:
    """successful_payment 已由 Telegram 保证真实;仍校验订单、币种与金额。返回 verdict。"""
    order = get_order(order_id)
    if not order:
        return "unknown_order"
    if currency != "XTR":
        verdict = "wrong_asset"
    elif amount != order.stars:
        verdict = "underpaid" if amount < (order.stars or 0) else "overpaid"
    else:
        verdict = "exact"
    if not claim_event(f"stars:{charge_id}", order_id, "stars", verdict, str(amount)):
        return "duplicate"
    return _apply(order, verdict, f"stars:{charge_id[:12]}")


def settle_ton(event: ton.ChainEvent) -> tuple[str, str | None]:
    """返回 (verdict, order_id)。memo 校验失败或找不到订单 → ("not_mine", None)。"""
    oid = ton.memo_valid(event.comment, _secret())
    order = get_order(oid) if oid else None
    if not order or not order.ton_memo:
        return "not_mine", None
    intent = ton.PaymentIntent(order.id, get_ton_config().get("address", ""), order.ton_nano or 0, "TON",
                               order.ton_memo, (order.expires_at or 0) + 3600)   # 报价过期后 1 小时内仍可识别为"迟到"
    verdict = ton.match(intent, event)
    # 到账时间晚于原始报价有效期但仍在宽限内 → 迟到,走人工
    if verdict == "exact" and event.ts > (order.expires_at or 0):
        verdict = "expired_late"
    if verdict == "not_mine":
        return verdict, None
    if not claim_event(f"ton:{event.network}:{event.tx_hash}:{event.lt}", order.id, "ton", verdict, str(event.amount_nano)):
        return "duplicate", order.id
    return _apply(order, verdict, f"ton:{event.tx_hash[:12]}"), order.id


def as_fsm(order: Order) -> FsmOrder:
    return FsmOrder(order.id, order.state)


# ---------- 自定义报价与 GMPay 收款(WP8) ----------
CUSTOM_SKU = "custom"
EXPIRED_GRACE_S = 3600


def parse_amount(text: str) -> Decimal:
    """'288'、'288.5'、'288 USDT' → Decimal;≤0.01 或非法抛 ValueError。金额一律 Decimal,不用浮点。"""
    try:
        v = Decimal(str(text).lower().replace("usdt", "").replace("u", "").strip().replace(",", ""))
    except InvalidOperation:
        raise ValueError("amount") from None
    if not v.is_finite() or v <= Decimal("0.01") or v.as_tuple().exponent < -2:
        raise ValueError("amount")
    return v


def create_custom_quote(user_id: int, *, title: str, detail: str, amount: str, next_step: str, lang: str = "zh",
                        created_by: int | None = None, source_case_id: int | None = None) -> Order:
    from ..config import cfg
    amt = parse_amount(amount)
    hours = float(cfg.get("payments.quote_valid_hours", default=24))
    oid = uuid.uuid4().hex[:10].upper()
    row = Order(id=oid, user_id=user_id, sku=CUSTOM_SKU, state="quoted", lang=lang, title=title[:200], detail=detail[:1000],
                next_step=next_step[:500], currency="USDT", base_amount_str=format(amt, "f"), expires_at=time.time() + hours * 3600,
                created_by=created_by, source_case_id=source_case_id)
    with SessionMaker() as db:            # 自定义订单互不作废:每张都是独立报价
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
    return row


async def attach_gateway(order_id: str, network: str, token: str, *, client=None) -> Order:
    """客户选定链/币后向 GMPay 下单并写入订单。同一链重复点击复用已有网关单;换链则另开网关单(旧单自然过期)。"""
    order = get_order(order_id)
    if not order or order.state not in ("quoted", "awaiting_payment"):
        raise KeyError(order_id)
    if order.gm_trade_id and order.network == network.lower() and (order.pay_token or "").lower() == token.lower():
        return order
    with SessionMaker() as db:
        n = db.query(PaymentEvent).filter(PaymentEvent.order_id == order_id, PaymentEvent.kind == "gmpay_intent").count()
        db.add(PaymentEvent(event_key=f"gmpay_intent:{order_id}:{n}", order_id=order_id, kind="gmpay_intent", verdict="created", amount_raw=""))
        db.commit()
    gw_order_id = order_id if n == 0 else f"{order_id}-{n}"
    intent = await gmpay.create_order(gw_order_id, Decimal(order.base_amount_str), network=network, token=token, name=order.title, client=client)
    with SessionMaker() as db:
        row = db.get(Order, order_id)
        row.gm_trade_id, row.pay_address, row.amount_str = intent.trade_id, intent.address, intent.amount
        row.network, row.pay_token, row.pay_url = intent.network, intent.token, intent.pay_url
        db.commit()
    transition(order_id, "awaiting_payment", actor="customer")
    return get_order(order_id)


def _tolerance_ok(expected: Decimal, received: Decimal) -> bool:
    from ..config import cfg
    t = cfg.get("payments.tolerance", default={"abs_u": "1", "pct": "1"})
    allow = min(Decimal(str(t.get("abs_u", "1"))), expected * Decimal(str(t.get("pct", "1"))) / 100)
    return expected - received <= allow


def settle_gmpay(trade_id: str, *, txid: str = "", actual_amount: str | None = None, source: str = "poll") -> tuple[str, str | None]:
    """网关确认已付(验签回调或查单=2)。返回 (verdict, order_id);不是本系统订单 → ("not_mine", None)。

    verdict:exact / tolerance(容差放行,按 exact 入账) / underpaid / expired_late / duplicate / orphan_payment。
    """
    with SessionMaker() as db:
        row = db.query(Order).filter(Order.gm_trade_id == trade_id).first()
        if not row:
            return "not_mine", None
        db.expunge(row)
    order = row
    verdict = "exact"
    if actual_amount is not None and order.amount_str:
        try:
            exp, got = Decimal(order.amount_str), Decimal(str(actual_amount))
            if got < exp:
                verdict = "tolerance" if _tolerance_ok(exp, got) else "underpaid"
            elif got > exp * Decimal("1.01") + Decimal("1"):
                verdict = "overpaid"
        except InvalidOperation:
            verdict = "underpaid"
    if verdict in ("exact", "tolerance") and time.time() > (order.expires_at or 0) + EXPIRED_GRACE_S:
        verdict = "expired_late"
    if not claim_event(f"gmpay:{trade_id}", order.id, "gmpay", verdict, str(actual_amount or "")):
        return "duplicate", order.id
    applied = _apply(order, "exact" if verdict == "tolerance" else verdict, f"gmpay:{source}")
    with SessionMaker() as db:
        r = db.get(Order, order.id)
        if txid:
            r.paid_txid = txid[:128]
        if applied == "exact":
            r.paid_at = datetime.now(timezone.utc)
        db.commit()
    return ("tolerance" if verdict == "tolerance" and applied == "exact" else applied), order.id


def gateway_pending() -> list[Order]:
    with SessionMaker() as db:
        rows = db.query(Order).filter(Order.gm_trade_id.isnot(None), Order.state.in_(("awaiting_payment", "quoted"))).all()
        db.expunge_all()
        return rows
