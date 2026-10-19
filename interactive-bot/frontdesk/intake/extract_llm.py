"""可选的模型抽取(03 §5.4,R-INTAKE-6):默认关闭,只"填空"不覆盖规则结果。

触发:进入某业务线问答时首条文本 ≥25 字且规则填充后必填槽位仍缺失。
约束:每 case ≤1 次;每日全局 ≤ daily_cap;max_tokens=300;超时 6 秒;熔断打开或预算耗尽不调用;
      返回必须是合法 JSON,字段值必须在选项 code 内,否则丢弃;成本记入 AiUsageLedger(event="extract")。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import timedelta

from sqlalchemy import func

from db.database import SessionMaker
from db.model import AiUsageLedger

from ...config import cfg
from . import engine

logger = logging.getLogger(__name__)
TIMEOUT = 6.0


def enabled() -> bool:
    return bool(cfg.get("intake.llm_extract.enabled", default=False))


def daily_cap() -> int:
    return int(cfg.get("intake.llm_extract.daily_cap", default=300))


def _used_today() -> int:
    from ...frontdesk import budget
    with SessionMaker() as db:
        return db.query(func.count(AiUsageLedger.id)).filter(AiUsageLedger.event == "extract",
                                                              AiUsageLedger.created_at >= budget._day_start_utc()).scalar() or 0


def _log(user_id: int, ok: bool) -> None:
    with SessionMaker() as db:
        db.add(AiUsageLedger(user_id=user_id, role="extract", event="extract", reply_units=0, reservation_key=f"extract-{uuid.uuid4().hex}"))
        db.commit()


def _prompt(flow: dict, text: str, missing: list[dict]) -> str:
    spec = []
    for s in missing:
        if s["kind"] == "text":
            spec.append(f'"{s["key"]}": string (<=200 chars) or null')
        else:
            codes = [c for c, _ in engine._options(s, "zh")]
            spec.append(f'"{s["key"]}": one of {json.dumps(codes, ensure_ascii=False)} or null')
    return ("Extract fields from the customer's message. Reply with ONE JSON object only, no prose. "
            "Use null when the message does not clearly state the value. Never guess.\n"
            "Fields:\n  " + "\n  ".join(spec) + f"\nMessage: {text[:600]}")


def validate(flow: dict, raw: str, missing: list[dict]) -> dict:
    """解析并校验;任何不合法字段丢弃。"""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {}
    out = {}
    for s in missing:
        v = data.get(s["key"])
        if v in (None, ""):
            continue
        if s["kind"] == "text":
            if isinstance(v, str) and 2 <= len(v) <= 200:
                out[s["key"]] = v.strip()
        else:
            codes = {c for c, _ in engine._options(s, "zh")}
            if isinstance(v, str) and v in codes and not (s["key"] == "line"):
                out[s["key"]] = v
    return out


async def maybe_extract(user_id: int, text: str, *, llm, owner_id: int = 0) -> list[str]:
    """符合条件时调用一次模型补填槽位;返回新填充的槽位名(失败/不满足条件返回 [])。"""
    if not enabled() or len((text or "").strip()) < 25 or llm is None:
        return []
    from whitecat_core.runtime import llm_breaker
    from ...frontdesk import budget
    if not llm_breaker.allow() or budget.exhausted() or _used_today() >= daily_cap():
        return []
    a = engine.active_state(user_id, owner_id)
    if not a:
        return []
    _, st = a
    if st.get("llm_extracted"):
        return []
    flow = engine._flow(st)
    unfilled = [s for s in flow.get("slots", []) if s["key"] not in st["slots"] and s["key"] not in st["skipped"] and s["key"] != "line"]
    if not any(s.get("required") for s in unfilled):             # 触发条件:必填槽位仍缺失
        return []
    missing = unfilled                                           # 触发后一并补填所有空槽位
    _log(user_id, True)
    try:
        raw = await asyncio.wait_for(llm.chat(system_prompt="You extract structured fields. Output JSON only.",
                                               messages=[{"role": "user", "content": _prompt(flow, text, missing)}],
                                               rag_context="", long_term_context=""), TIMEOUT)
        llm_breaker.success()
    except Exception as exc:
        llm_breaker.failure()
        logger.warning("llm extract failed: %s", type(exc).__name__)
        raw = ""
    values = validate(flow, raw, missing)
    filled = []

    def run(st, c):
        st["llm_extracted"] = True
        for k, v in values.items():
            if k not in st["slots"]:
                engine._fill(st, k, v, "llm")
                filled.append(k)
    engine._mutate(user_id, owner_id, run)
    for k in filled:
        from ...analytics.events import emit
        emit("intake_answered", user_id, slot=k[:32], method="llm")
    return filled
