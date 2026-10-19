"""关键卡片的 message_id 登记(ui_message 表):用于原位更新、收起、置顶与取消置顶。"""
from __future__ import annotations

from db.database import SessionMaker
from db.model import UiMessage


def _d(r: UiMessage) -> dict:
    return {"id": r.id, "user_id": r.user_id, "chat_id": r.chat_id, "message_id": r.message_id, "biz": r.biz_conn_id, "kind": r.kind,
            "ref": r.ref, "pinned": bool(r.pinned), "state": r.state, "unpin_after": r.unpin_after}


def record(user_id: int, chat_id: int, message_id: int, kind: str, *, ref=None, biz: str | None = None, pinned: bool = False) -> int:
    with SessionMaker() as db:
        row = UiMessage(user_id=user_id, chat_id=chat_id, message_id=message_id, kind=kind, ref=str(ref) if ref is not None else None,
                        biz_conn_id=biz, pinned=pinned)
        db.add(row)
        db.commit()
        return row.id


def get(ui_id: int) -> dict | None:
    with SessionMaker() as db:
        r = db.get(UiMessage, ui_id)
        return _d(r) if r else None


def by_message(chat_id: int, message_id: int) -> dict | None:
    with SessionMaker() as db:
        r = db.query(UiMessage).filter(UiMessage.chat_id == chat_id, UiMessage.message_id == message_id).order_by(UiMessage.id.desc()).first()
        return _d(r) if r else None


def latest(user_id: int, kind: str, *, ref=None, state: str | None = "live") -> dict | None:
    with SessionMaker() as db:
        q = db.query(UiMessage).filter(UiMessage.user_id == user_id, UiMessage.kind == kind)
        if ref is not None:
            q = q.filter(UiMessage.ref == str(ref))
        if state:
            q = q.filter(UiMessage.state == state)
        r = q.order_by(UiMessage.id.desc()).first()
        return _d(r) if r else None


def live(user_id: int, kind: str) -> list[dict]:
    with SessionMaker() as db:
        return [_d(r) for r in db.query(UiMessage).filter(UiMessage.user_id == user_id, UiMessage.kind == kind, UiMessage.state == "live").all()]


def set_state(ui_id: int, state: str) -> None:
    with SessionMaker() as db:
        r = db.get(UiMessage, ui_id)
        if r:
            r.state = state
            db.commit()


def mark_pinned(ui_id: int, pinned: bool, unpin_after: float | None = None) -> None:
    with SessionMaker() as db:
        r = db.get(UiMessage, ui_id)
        if r:
            r.pinned = pinned
            r.unpin_after = unpin_after
            db.commit()


def due_unpins(now_ts: float) -> list[dict]:
    with SessionMaker() as db:
        return [_d(r) for r in db.query(UiMessage).filter(UiMessage.pinned.is_(True), UiMessage.unpin_after.isnot(None),
                                                          UiMessage.unpin_after <= now_ts).all()]
