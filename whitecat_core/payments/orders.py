"""订单状态机与链上交易唯一认领。付款截图/自述永远只能到 payment_review。"""
from __future__ import annotations

from dataclasses import dataclass, field

TRANSITIONS: dict[str, set[str]] = {
    "draft": {"quoted", "cancelled"},
    "quoted": {"awaiting_payment", "superseded", "cancelled"},
    "awaiting_payment": {"payment_review", "paid", "superseded", "cancelled"},
    "payment_review": {"paid", "awaiting_payment", "disputed", "cancelled"},
    "paid": {"fulfilling", "refunded", "disputed"},
    "fulfilling": {"delivered", "refunded", "disputed"},
    "delivered": {"refunded", "disputed"},
    "disputed": {"refunded", "delivered"},
    "cancelled": set(), "refunded": set(), "superseded": set(),
}
# 只有携带"已验证事实"才能进入的状态
VERIFIED_ONLY = {"paid"}


class IllegalTransition(Exception):
    pass


@dataclass
class Order:
    id: str
    state: str = "draft"
    history: list[tuple[str, str, str]] = field(default_factory=list)   # (from, to, actor)

    def move(self, to: str, *, actor: str, verified: bool = False) -> None:
        if to not in TRANSITIONS.get(self.state, ()):
            raise IllegalTransition(f"{self.state} -> {to}")
        if to in VERIFIED_ONLY and not verified:
            raise IllegalTransition(f"{to} requires verified payment fact")
        self.history.append((self.state, to, actor))
        self.state = to


class TxLedger:
    """交易唯一键 (network, tx_hash, lt) 只能被一个订单认领;生产中为数据库 UNIQUE 约束。"""

    def __init__(self) -> None:
        self._claims: dict[tuple, str] = {}

    def claim(self, key: tuple, order_id: str) -> bool:
        owner = self._claims.setdefault(key, order_id)
        return owner == order_id

    def owner(self, key: tuple) -> str | None:
        return self._claims.get(key)
