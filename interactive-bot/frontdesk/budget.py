"""全局 LLM 预算:按上海自然日统计,价格由运营配置(美元/百万 token)。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from db.database import SessionMaker
from db.model import AiUsageLedger

from ..global_settings import get_global_setting
from .service import POLICY

logger = logging.getLogger(__name__)
_SH = timezone(timedelta(hours=8))
DEFAULT_DAILY_REPLY_CAP = 3000      # 未配置价格时的兜底:全站每日成功回复上限


def prices() -> tuple[float, float] | None:
    pin, pout = get_global_setting("llm_price_in_per_m"), get_global_setting("llm_price_out_per_m")
    try:
        return (float(pin), float(pout)) if pin is not None and pout is not None else None
    except (TypeError, ValueError):
        return None


def cost_usd(tokens_in: int, tokens_out: int) -> float:
    p = prices()
    return 0.0 if not p else (tokens_in * p[0] + tokens_out * p[1]) / 1_000_000


def daily_budget() -> float:
    v = get_global_setting("global_daily_budget_usd")
    return float(v) if v is not None else float(POLICY["quotas"]["global_daily_cost_usd"])


def _day_start_utc() -> datetime:
    now = datetime.now(_SH)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)


def exhausted() -> str | None:
    """返回耗尽原因('budget'/'reply_cap')或 None。已配置价格 → 按美元;否则按每日回复数兜底。"""
    with SessionMaker() as db:
        since = _day_start_utc()
        if prices():
            spent = db.query(func.coalesce(func.sum(AiUsageLedger.cost_usd), 0.0)).filter(AiUsageLedger.created_at >= since).scalar()
            return "budget" if spent >= daily_budget() else None
        n = db.query(func.count(AiUsageLedger.id)).filter(AiUsageLedger.event == "success", AiUsageLedger.created_at >= since).scalar()
        cap = int(get_global_setting("global_daily_reply_cap") or DEFAULT_DAILY_REPLY_CAP)
        return "reply_cap" if n >= cap else None
