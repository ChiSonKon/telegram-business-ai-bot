"""GMPay v2 / EPUSDT 网关适配(05 §5 的收款通道,取代自建链上轮询)。

- 商户 PID 与密钥只来自环境变量(GMPAY_PID / GMPAY_SECRET),不进配置中心、不写日志、不经过模型。
- 下单 POST /payments/gmpay/v1/order/create-transaction,查单 GET /pay/check-status/{trade_id}(无需签名)。
- 签名:剔除 signature 与空值,按键名排序拼 k=v&…,HMAC-SHA256(密钥) 小写十六进制;回调同法验签。
- 金额以美元计价(currency=usd),网关对 USDT 为 1:1;付款成立只信「验签通过的回调」或「网关查单状态=2」。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import math
import os
from dataclasses import dataclass
from decimal import Decimal

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://pay.web3baimao.com"
STATUS_WAITING, STATUS_PAID, STATUS_EXPIRED, STATUS_SELECT = 1, 2, 3, 4


class GatewayError(Exception):
    """网关不可用/拒绝。消息里不含任何密钥。"""


@dataclass(frozen=True)
class Intent:
    trade_id: str
    address: str
    amount: str           # 客户应付的加密货币数量(网关给出)
    token: str
    network: str
    expires_at: float
    pay_url: str


def config() -> dict:
    return {"base": os.environ.get("GMPAY_BASE", DEFAULT_BASE).rstrip("/"), "pid": os.environ.get("GMPAY_PID", ""),
            "secret": os.environ.get("GMPAY_SECRET", ""), "notify_url": os.environ.get("GMPAY_NOTIFY_URL", "")}


def configured() -> bool:
    c = config()
    return bool(c["pid"] and c["secret"])


def _canonical_value(value) -> str:
    if isinstance(value, bool):
        raise TypeError("booleans are not allowed in signed params")
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite number")
        if value.is_integer():
            return str(int(value))
        text = repr(value)
        return format(Decimal(text), "f") if "e" in text.lower() else text
    return str(value)


def sign(params: dict, secret: str) -> str:
    pairs = []
    for k, v in params.items():
        if k == "signature" or v is None:
            continue
        t = _canonical_value(v)
        if t != "":
            pairs.append(f"{k}={t}")
    return hmac.new(secret.encode(), "&".join(sorted(pairs)).encode(), hashlib.sha256).hexdigest()


def verify_callback(payload: dict) -> bool:
    """回调验签:PID 必须是本商户;签名常数时间比较。"""
    c = config()
    if not c["secret"] or str(payload.get("pid", "")) != c["pid"]:
        return False
    given = str(payload.get("signature", ""))
    try:
        return hmac.compare_digest(given, sign(payload, c["secret"]))
    except (TypeError, ValueError):
        return False


async def _request(method: str, url: str, *, json=None, client: httpx.AsyncClient | None = None) -> dict:
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        r = await client.request(method, url, json=json, headers={"Accept": "application/json", "User-Agent": "whitecat-bot/1.0"})
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GatewayError(type(exc).__name__) from None
    finally:
        if own:
            await client.aclose()
    if not isinstance(data, dict) or data.get("status_code") != 200:
        raise GatewayError(str((data or {}).get("message", "rejected"))[:80] if isinstance(data, dict) else "bad response")
    return data.get("data") or {}


async def supported_assets(client=None) -> list[dict]:
    """[{network, display_name, tokens:[...]}],取自网关当前可用组合(链已启用且有钱包地址)。"""
    c = config()
    data = await _request("GET", f"{c['base']}/payments/gmpay/v1/config", client=client)
    return [a for a in data.get("supported_assets", []) if a.get("network") and a.get("tokens")]


async def create_order(order_id: str, amount: Decimal, *, network: str, token: str, name: str = "", client=None) -> Intent:
    """order_id ≤ 32 字符且不可重复(用本地订单号;同一订单换链需换后缀,调用方负责)。"""
    c = config()
    if not (c["pid"] and c["secret"]):
        raise GatewayError("not configured")
    if len(order_id) > 32:
        raise GatewayError("order id too long")
    body = {"pid": c["pid"], "order_id": order_id, "currency": "usd", "token": token.lower(), "network": network.lower(),
            "amount": float(amount), "notify_url": c["notify_url"] or f"{c['base']}/notify-unused", "name": name[:60]}
    body["signature"] = sign(body, c["secret"])
    d = await _request("POST", f"{c['base']}/payments/gmpay/v1/order/create-transaction", json=body, client=client)
    if not d.get("trade_id") or not d.get("receive_address"):
        raise GatewayError("incomplete response")
    return Intent(str(d["trade_id"]), str(d["receive_address"]), _canonical_value(d.get("actual_amount", 0)),
                  str(d.get("token", token)).upper(), network.lower(), float(d.get("expiration_time") or 0), str(d.get("payment_url", "")))


async def check_status(trade_id: str, client=None) -> int:
    """1 待付 / 2 已付 / 3 过期 / 4 待选链。网络失败抛 GatewayError,由轮询下一轮重试。"""
    c = config()
    d = await _request("GET", f"{c['base']}/pay/check-status/{trade_id}", client=client)
    try:
        return int(d.get("status"))
    except (TypeError, ValueError):
        raise GatewayError("bad status") from None
