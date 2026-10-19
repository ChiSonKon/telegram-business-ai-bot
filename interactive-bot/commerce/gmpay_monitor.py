"""GMPay 入账监听:轮询未付网关单的状态(无需公网回调);回调入口 handle_callback 已备好,待配置 HTTPS 后启用。"""
from __future__ import annotations

import logging

from . import service
from .notify import notify_settlement
from .providers import gmpay

logger = logging.getLogger(__name__)


async def poll_gmpay(bot, checker=gmpay.check_status) -> int:
    """返回本轮新确认的订单数。网关不可用只记日志,下一轮重试(认领表保证幂等)。"""
    if not gmpay.configured():
        return 0
    n = 0
    for order in service.gateway_pending():
        try:
            status = await checker(order.gm_trade_id)
        except Exception as exc:
            logger.warning("gmpay status failed: %s", type(exc).__name__)
            continue
        if status != gmpay.STATUS_PAID:
            continue
        verdict, oid = service.settle_gmpay(order.gm_trade_id, source="poll")
        if oid and verdict not in ("duplicate", "not_mine"):
            n += 1
            await notify_settlement(bot, oid, verdict)
    return n


async def handle_callback(bot, payload: dict) -> bool:
    """验签通过且 status=2 才结算;返回是否应回复网关 'ok'(验签失败 False)。"""
    if not gmpay.verify_callback(payload):
        return False
    if int(payload.get("status", 0)) == gmpay.STATUS_PAID:
        verdict, oid = service.settle_gmpay(str(payload.get("trade_id", "")), txid=str(payload.get("block_transaction_id", "")),
                                            actual_amount=str(payload.get("actual_amount", "")), source="callback")
        if oid and verdict not in ("duplicate", "not_mine"):
            await notify_settlement(bot, oid, verdict)
    return True
