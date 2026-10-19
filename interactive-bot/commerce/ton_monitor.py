"""TON 入账监听:轮询配置地址的最近交易,按 memo+金额+有效期匹配订单。

数据源为 toncenter v3 `/api/v3/transactions`(可通过 commerce.ton.api_base 换成自建索引器)。
解析逻辑只依赖:tx.hash / tx.lt / tx.now / tx.in_msg.{source,value,message_content.decoded.comment}。
因为查询已限定 account=配置地址,凡带 source 的入站消息即发往该地址,无需比对地址字符串格式。
"""
from __future__ import annotations

import logging

import httpx
from whitecat_core.payments.ton import ChainEvent

from . import service
from .notify import notify_settlement

logger = logging.getLogger(__name__)


def parse_events(payload: dict, address: str) -> list[ChainEvent]:
    out: list[ChainEvent] = []
    for tx in payload.get("transactions", []):
        msg = tx.get("in_msg") or {}
        if not msg.get("source"):                      # 外部消息/无来源:不是入账
            continue
        try:
            value = int(msg.get("value") or 0)
            decoded = (msg.get("message_content") or {}).get("decoded") or {}
            comment = decoded.get("comment") or ""
            if value <= 0 or not comment:
                continue
            out.append(ChainEvent("ton", str(tx["hash"]), int(tx["lt"]), address, value, "TON", comment, float(tx["now"])))
        except (KeyError, ValueError, TypeError):
            continue
    return out


async def fetch(cfg: dict, client: httpx.AsyncClient | None = None) -> dict:
    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        headers = {"X-API-Key": cfg["api_key"]} if cfg.get("api_key") else {}
        r = await client.get(f"{cfg.get('api_base', 'https://toncenter.com').rstrip('/')}/api/v3/transactions",
                             params={"account": cfg["address"], "limit": 30, "sort": "desc"}, headers=headers)
        r.raise_for_status()
        return r.json()
    finally:
        if own:
            await client.aclose()


async def poll_ton(bot, fetcher=fetch) -> int:
    """返回本轮新认领的事件数。网络失败只记日志,下一轮重试(认领表保证幂等)。"""
    cfg = service.get_ton_config()
    if not cfg.get("address"):
        return 0
    try:
        payload = await fetcher(cfg)
    except Exception as exc:
        logger.warning("TON poll failed: %s", type(exc).__name__)
        return 0
    n = 0
    for ev in reversed(parse_events(payload, cfg["address"])):    # 由旧到新
        verdict, oid = service.settle_ton(ev)
        if verdict in ("not_mine", "duplicate") or not oid:
            continue
        n += 1
        await notify_settlement(bot, oid, verdict)
    return n
