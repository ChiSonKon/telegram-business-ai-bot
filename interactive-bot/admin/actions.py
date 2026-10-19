"""控制台写操作:每一步都校验当前状态、写审计,并让在途 AI 任务失效。"""
from __future__ import annotations

import uuid

from db.database import SessionMaker
from db.model import AdminAudit, AiUsageLedger, FrontdeskCase

from ..frontdesk.service import ROLE_LIMITS, admin_takeover, epochs, _ekey
from ..middleware.state_machine import ChatMode, set_chat_mode

RELEASE_REASONS = {
    "need": "客户补充了新的业务需求",
    "solved": "问题已处理，客户想继续咨询",
    "other": "其他原因",
}
AI_STATE = {"prospect": "AI_SALES", "support": "AI_SUPPORT"}


def audit(admin_id: int, action: str, target: str = "", detail: str = "") -> None:
    with SessionMaker() as db:
        db.add(AdminAudit(admin_id=admin_id, action=action, target=str(target), detail=detail[:500]))
        db.commit()


def _case(db, uid: int):
    return db.query(FrontdeskCase).filter_by(user_id=uid, business_owner_id=0).order_by(FrontdeskCase.id.desc()).first()


def take_over(admin_id: int, uid: int) -> bool:
    with SessionMaker() as db:
        c = _case(db, uid)
        if not c or c.state in ("HUMAN_ACTIVE", "RESOLVED"):
            return False
    admin_takeover(uid)
    set_chat_mode(uid, ChatMode.HUMAN_MODE)
    audit(admin_id, "接管", uid)
    return True


def resolve(admin_id: int, uid: int) -> bool:
    with SessionMaker() as db:
        c = _case(db, uid)
        if not c or c.state == "RESOLVED":
            return False
        # 同一客户可能留有多条历史 case(例如点过"这是另一个新问题"):全部结案,否则旧的会重新出现在待跟进里
        for old in db.query(FrontdeskCase).filter(FrontdeskCase.user_id == uid, FrontdeskCase.business_owner_id == 0,
                                                  FrontdeskCase.state.in_(("HUMAN_PENDING", "HUMAN_ACTIVE"))).all():
            old.state = "RESOLVED"
        c.state = "RESOLVED"
        db.commit()
    epochs.bump(_ekey(uid))
    set_chat_mode(uid, ChatMode.AI_MODE, admin_release=True)      # 客户下次来消息会重新预检,而不是被静默
    audit(admin_id, "标记已解决", uid)
    return True


def release_to_ai(admin_id: int, uid: int, reason_code: str) -> str | None:
    """恢复 AI 必须说明原因并写审计。仅咨询/售后角色可恢复;返回恢复后的状态,否则 None。"""
    with SessionMaker() as db:
        c = _case(db, uid)
        if not c or c.state not in ("HUMAN_PENDING", "HUMAN_ACTIVE") or c.role not in AI_STATE:
            return None
        c.state = AI_STATE[c.role]
        db.commit()
        state = c.state
    set_chat_mode(uid, ChatMode.AI_MODE, admin_release=True)
    audit(admin_id, "恢复AI", uid, RELEASE_REASONS.get(reason_code, reason_code))
    return state


def grant_replies(admin_id: int, uid: int, n: int = 6) -> int | None:
    """为该客户增加 n 轮 AI(记账为负的已用量),受审计;返回新的已用量。"""
    with SessionMaker() as db:
        c = _case(db, uid)
        if not c or c.role not in ROLE_LIMITS or ROLE_LIMITS[c.role] == 0:
            return None
        db.add(AiUsageLedger(user_id=uid, role=c.role, event="grant", reply_units=-n, reservation_key=f"grant-{uuid.uuid4().hex}"))
        c.extensions = (c.extensions or 0) + 1
        db.commit()
    audit(admin_id, "增加AI轮数", uid, f"+{n}")
    return n
