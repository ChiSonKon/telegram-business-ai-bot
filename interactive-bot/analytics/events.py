"""漏斗埋点。emit() 绝不影响主流程:写库失败只记日志;未知事件名抛 ValueError(让拼写错误在测试中暴露)。

隐私:props 只允许 bool/int/float,以及 ≤32 字符、仅含 ASCII 字母数字/_:.-/ 的枚举样字符串;
任何看起来像客户原文的值直接丢弃。
"""
from __future__ import annotations

import json
import logging
import re

from db.database import SessionMaker
from db.model import FrontdeskCase, FunnelEvent

logger = logging.getLogger(__name__)

EVENTS = frozenset({
    "entry", "first_value", "answer_card_shown",
    "intake_started", "intake_step_shown", "intake_answered", "intake_skipped", "intake_side_question",
    "intake_completed", "intake_abandoned", "intake_corrected",
    "ai_turn", "ai_effective", "ai_stopped", "cta_shown", "cta_clicked",
    "handoff", "human_first_reply",
    "quote_sent", "pay_clicked", "txid_submitted", "payment_verified", "payment_review", "delivered",
    "self_resolved", "verify_check", "impersonation_report", "returned",
    "interest_added", "effect_sent", "pin_set", "pin_cleared", "config_published", "config_rolled_back",
})

_ENUM = re.compile(r"^[A-Za-z0-9_:.\-/]{1,32}$")


def _clean_props(props: dict) -> dict:
    out = {}
    for k, v in props.items():
        if v is None:
            continue
        if isinstance(v, (bool, int, float)):
            out[k] = v
        elif isinstance(v, str) and _ENUM.match(v):
            out[k] = v
        else:
            logger.warning("analytics: dropped non-enum prop %s", k)
    return out


def emit(event: str, user_id: int, case_id: int | None = None, *, channel: str | None = None,
         source: str | None = None, line: str | None = None, **props) -> None:
    if event not in EVENTS:
        raise ValueError(f"unknown analytics event: {event}")
    try:
        from ..admin import sandbox
        clean = _clean_props(props)
        with SessionMaker() as db:
            case = db.get(FrontdeskCase, case_id) if case_id else \
                db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=0).order_by(FrontdeskCase.id.desc()).first()
            if case:
                case_id = case_id or case.id
                channel = channel or case.channel
                source = source or case.source or _source_from_answers(case)
                if line is None:
                    line = _line_from_answers(case)
            db.add(FunnelEvent(user_id=user_id, case_id=case_id, channel=(channel or "bot")[:16], source=(source or None),
                               event=event, line=line, props_json=json.dumps(clean, ensure_ascii=False),
                               test=bool(sandbox.is_client_view(user_id))))
            db.commit()
    except Exception as exc:                         # 埋点永远不能拖垮主流程
        logger.warning("analytics emit failed (%s): %s", event, type(exc).__name__)


def _answers(case) -> dict:
    try:
        return json.loads(case.answers_json or "{}")
    except ValueError:
        return {}


def _source_from_answers(case) -> str | None:
    s = _answers(case).get("source")
    return s if isinstance(s, str) and _ENUM.match(s) else None


def _line_from_answers(case) -> str | None:
    s = _answers(case).get("line")
    return s if isinstance(s, str) and _ENUM.match(s) else None
