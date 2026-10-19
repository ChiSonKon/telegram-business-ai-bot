"""TON 转账意图:唯一 memo、深链、链上事件匹配。地址只来自配置,绝不由模型生成。"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from urllib.parse import quote

NANO = 10**9


@dataclass(frozen=True)
class PaymentIntent:
    order_id: str
    address: str
    amount_nano: int
    asset: str            # "TON" 或 jetton 标识(如 "USDT")
    memo: str
    expires_at: float     # unix 秒
    tolerance_nano: int = 0   # 链上手续费/四舍五入容差(默认严格)


@dataclass(frozen=True)
class ChainEvent:
    network: str
    tx_hash: str
    lt: int
    to_address: str
    amount_nano: int
    asset: str
    comment: str
    ts: float

    @property
    def key(self) -> tuple:
        return (self.network, self.tx_hash, self.lt)


def make_memo(order_id: str, secret: bytes) -> str:
    """订单号 + HMAC 校验位,防止被他人猜测/伪造他人订单的 memo。"""
    mac = hmac.new(secret, order_id.encode(), hashlib.sha256).hexdigest()[:6]
    return f"WC-{order_id}-{mac}"


def memo_valid(memo: str, secret: bytes) -> str | None:
    """校验通过返回 order_id,否则 None。"""
    if not memo.startswith("WC-") or memo.count("-") < 2:
        return None
    body, _, mac = memo[3:].rpartition("-")
    expected = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()[:6]
    return body if body and hmac.compare_digest(mac, expected) else None


def build_link(intent: PaymentIntent) -> str:
    """仅适用 TON 原生币;jetton 需经钱包适配器生成。"""
    if intent.asset != "TON":
        raise ValueError("ton:// 深链仅支持 TON 原生币")
    return f"ton://transfer/{intent.address}?amount={intent.amount_nano}&text={quote(intent.memo)}"


def match(intent: PaymentIntent, ev: ChainEvent) -> str:
    """返回: not_mine / wrong_asset / expired_late / underpaid / overpaid / exact。
    只有 exact 允许自动推进;其余均交人工并保留事实。"""
    if ev.to_address != intent.address or ev.comment != intent.memo:
        return "not_mine"
    if ev.asset != intent.asset:
        return "wrong_asset"
    if ev.ts > intent.expires_at:
        return "expired_late"
    if ev.amount_nano < intent.amount_nano - intent.tolerance_nano:
        return "underpaid"
    if ev.amount_nano > intent.amount_nano + intent.tolerance_nano:
        return "overpaid"
    return "exact"


def settle(intent: PaymentIntent, ev: ChainEvent, order, ledger) -> str:
    """幂等结算:返回 match 结果或 'duplicate'。仅 exact 且认领成功时订单 -> paid。"""
    verdict = match(intent, ev)
    if verdict == "not_mine":
        return verdict
    if not ledger.claim(ev.key, intent.order_id):
        return "duplicate"
    if verdict == "exact" and order.state in ("awaiting_payment", "payment_review"):
        order.move("paid", actor=f"chain:{ev.tx_hash[:12]}", verified=True)
    elif verdict != "exact" and order.state == "awaiting_payment":
        order.move("payment_review", actor=f"chain:{verdict}")
    return verdict


def build_wallet_link(intent: PaymentIntent) -> str:
    """https 通用转账链接(Telegram 内联按钮不接受 ton:// 协议)。"""
    if intent.asset != "TON":
        raise ValueError("仅支持 TON 原生币")
    return f"https://app.tonkeeper.com/transfer/{intent.address}?amount={intent.amount_nano}&text={quote(intent.memo)}"
