"""客户档案(CustomerProfile):披露状态、是否启动过机器人、来源、特效冷却等跨会话事实。

WP1 临时存放在 AppSetting 的 biz_disclosed:<uid> / bot_started:<uid> 在首次读取时迁入。
"""
from __future__ import annotations

from db.database import SessionMaker
from db.model import AppSetting, CustomerProfile


def _legacy(db, key: str) -> bool:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row:
        db.delete(row)
        return True
    return False


def _ensure(db, user_id: int) -> CustomerProfile:
    p = db.get(CustomerProfile, user_id)
    if not p:
        p = CustomerProfile(user_id=user_id, biz_disclosed=False, bot_started=False)
        db.add(p)
        db.flush()
    if _legacy(db, f"biz_disclosed:{user_id}"):
        p.biz_disclosed = True
    if _legacy(db, f"bot_started:{user_id}"):
        p.bot_started = True
    return p


def take_disclosure(user_id: int) -> bool:
    """Business 披露:首次调用返回 True 并记录,之后返回 False。"""
    with SessionMaker() as db:
        p = _ensure(db, user_id)
        if p.biz_disclosed:
            db.commit()
            return False
        p.biz_disclosed = True
        db.commit()
        return True


def disclosed(user_id: int) -> bool:
    """Business 披露行是否已发(不改变状态)。"""
    with SessionMaker() as db:
        p = db.get(CustomerProfile, user_id)
        if p and p.biz_disclosed:
            return True
        return db.query(AppSetting).filter(AppSetting.key == f"biz_disclosed:{user_id}").first() is not None


def mark_bot_started(user_id: int) -> None:
    with SessionMaker() as db:
        _ensure(db, user_id).bot_started = True
        db.commit()


def bot_started(user_id: int) -> bool:
    with SessionMaker() as db:
        p = _ensure(db, user_id)
        db.commit()
        return bool(p.bot_started)


def note_source(user_id: int, source: str) -> None:
    with SessionMaker() as db:
        p = _ensure(db, user_id)
        p.first_source = p.first_source or source[:64]
        p.last_source = source[:64]
        db.commit()


def note_lang(user_id: int, lang: str) -> None:
    with SessionMaker() as db:
        _ensure(db, user_id).lang = lang
        db.commit()


def delete(user_id: int) -> None:
    with SessionMaker() as db:
        p = db.get(CustomerProfile, user_id)
        if p:
            db.delete(p)
        db.commit()
