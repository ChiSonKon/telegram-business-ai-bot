"""定时任务:问答放弃判定。"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from db.database import SessionMaker
from db.model import FrontdeskCase

from .events import emit

logger = logging.getLogger(__name__)
ABANDON_AFTER_MIN = 30


def intake_abandon_job() -> int:
    """PRECHECK 且客户 30 分钟没有回应 → 每个 case 记一次 intake_abandoned(flow, slot)。返回新记录数。"""
    from whitecat_core.runtime import now
    from ..frontdesk import precheck
    cutoff = now() - timedelta(minutes=ABANDON_AFTER_MIN)
    n = 0
    with SessionMaker() as db:
        rows = db.query(FrontdeskCase).filter(FrontdeskCase.state == "PRECHECK", FrontdeskCase.last_customer_at.isnot(None)).all()
        todo = []
        for c in rows:
            last = c.last_customer_at if c.last_customer_at.tzinfo else c.last_customer_at.replace(tzinfo=cutoff.tzinfo)
            try:
                a = json.loads(c.answers_json or "{}")
            except ValueError:
                a = {}
            if last <= cutoff and not a.get("abandon_emitted"):
                a["abandon_emitted"] = True
                c.answers_json = json.dumps(a, ensure_ascii=False)
                todo.append((c.user_id, c.id, c.role, c.business_owner_id))
        db.commit()
    for uid, cid, role, owner in todo:
        emit("intake_abandoned", uid, cid, flow=role or "-", slot=precheck.current_step(uid, owner) or "-")
        n += 1
    return n
