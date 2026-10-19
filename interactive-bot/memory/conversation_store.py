"""对话记忆持久化:替换进程内的 ShortTermMemory,重启不失忆(R-AI-4)。

接口兼容 ShortTermMemory(add / get_history),可直接作为 run_ai_turn 的 short_memory。
同事(staff)的回复也保存;给模型看时以 assistant 角色 + "[同事] " 前缀注入,让 AI 知道人工说过什么。
"""
from __future__ import annotations

import logging
from datetime import timedelta

from db.database import SessionMaker
from db.model import ConversationTurn, FrontdeskCase

logger = logging.getLogger(__name__)

STAFF_PREFIX = "[同事] "
MAX_CHARS = 4000


class ConversationStore:
    def __init__(self, max_messages: int = 15):
        self.max_messages = max_messages

    def add(self, user_id: int, role: str, content: str, case_id: int | None = None) -> None:
        if role not in ("user", "assistant", "staff") or not (content or "").strip():
            return
        try:
            with SessionMaker() as db:
                if case_id is None:
                    c = db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=0).order_by(FrontdeskCase.id.desc()).first()
                    case_id = c.id if c else None
                from whitecat_core.runtime import now
                db.add(ConversationTurn(user_id=user_id, case_id=case_id, role=role, content=content[:MAX_CHARS],
                                        created_at=now().replace(tzinfo=None)))
                db.commit()
        except Exception as exc:                                 # 记忆写入失败不能影响客户收到回复
            logger.warning("conversation_store.add failed: %s", type(exc).__name__)

    def recent(self, user_id: int, n: int | None = None) -> list[dict]:
        n = n or self.max_messages
        with SessionMaker() as db:
            rows = db.query(ConversationTurn).filter(ConversationTurn.user_id == user_id).order_by(ConversationTurn.id.desc()).limit(n).all()
        out = []
        for r in reversed(rows):
            if r.role == "staff":
                out.append({"role": "assistant", "content": STAFF_PREFIX + r.content})
            else:
                out.append({"role": r.role, "content": r.content})
        return out

    def get_history(self, user_id: int) -> list[dict]:
        return self.recent(user_id, self.max_messages)

    def clear(self, user_id: int) -> None:
        with SessionMaker() as db:
            db.query(ConversationTurn).filter(ConversationTurn.user_id == user_id).delete()
            db.commit()

    def purge_older_than(self, days: int) -> int:
        from whitecat_core.runtime import now
        cutoff = (now() - timedelta(days=days)).replace(tzinfo=None)
        with SessionMaker() as db:
            n = db.query(ConversationTurn).filter(ConversationTurn.created_at < cutoff).delete()
            db.commit()
        return n
