"""客户视角模式:管理员用自己的账号以普通客户身份自测。

/asclient on|off|reset(仅管理员,私聊)。状态存 AppSetting `asclient:<admin_id>`。
前台路径判断"是否管理员"时调用 is_client_view(uid):为真则按普通客户处理。
"""
from __future__ import annotations

import logging

from sqlalchemy import delete

from db.database import SessionMaker
from db.model import (AiUsageLedger, AppSetting, ConversationTurn, CustomerProfile, FrontdeskCase, FrontdeskMessage, FunnelEvent,
                      MessageMap, UiMessage)

logger = logging.getLogger(__name__)


def _key(uid: int) -> str:
    return f"asclient:{uid}"


def is_client_view(uid: int) -> bool:
    try:
        with SessionMaker() as db:
            row = db.query(AppSetting).filter(AppSetting.key == _key(uid)).first()
            return bool(row and row.value == "on")
    except Exception:
        return False


def _dkey(uid: int) -> str:
    return f"asclient_draft:{uid}"


def is_draft_mode(uid: int) -> bool:
    try:
        with SessionMaker() as db:
            row = db.query(AppSetting).filter(AppSetting.key == _dkey(uid)).first()
            return bool(row and row.value == "on")
    except Exception:
        return False


def set_draft_mode(uid: int, on: bool) -> None:
    with SessionMaker() as db:
        row = db.query(AppSetting).filter(AppSetting.key == _dkey(uid)).first()
        if row:
            row.value = "on" if on else "off"
        else:
            db.add(AppSetting(key=_dkey(uid), value="on" if on else "off"))
        db.commit()


def scope_user(uid: int) -> int | None:
    """客户视角 + 草稿模式同时开启时返回 uid(读取配置时优先使用该管理员的草稿)。"""
    return uid if (is_client_view(uid) and is_draft_mode(uid)) else None


def set_client_view(uid: int, on: bool) -> None:
    with SessionMaker() as db:
        row = db.query(AppSetting).filter(AppSetting.key == _key(uid)).first()
        if row:
            row.value = "on" if on else "off"
        else:
            db.add(AppSetting(key=_key(uid), value="on" if on else "off"))
        db.commit()


def reset_client_state(uid: int) -> dict:
    """清空该 uid 作为"客户"留下的前台状态,便于重复走流程。"""
    from db.model import User
    counts = {}
    with SessionMaker() as db:
        user_row = db.query(User).filter(User.user_id == uid).first()
        if user_row:
            user_row.chat_mode = "AI_MODE"
        for model in (FrontdeskMessage, AiUsageLedger, FrontdeskCase):
            res = db.execute(delete(model).where(model.user_id == uid))
            counts[model.__tablename__] = res.rowcount
        res = db.execute(delete(MessageMap).where(MessageMap.user_id == uid))
        counts["message_map"] = res.rowcount
        db.execute(delete(AppSetting).where(AppSetting.key.in_([f"biz_disclosed:{uid}", f"bot_started:{uid}"])))
        db.execute(delete(CustomerProfile).where(CustomerProfile.user_id == uid))
        db.execute(delete(ConversationTurn).where(ConversationTurn.user_id == uid))
        db.execute(delete(UiMessage).where(UiMessage.user_id == uid))
        db.execute(delete(FunnelEvent).where(FunnelEvent.user_id == uid, FunnelEvent.test.is_(True)))
        db.commit()
    try:
        from whitecat_core import runtime
        runtime.epochs.bump(runtime.epoch_key(uid))
    except Exception:
        pass
    return counts


async def cmd_asclient(update, context):
    from .. import admin_user_ids
    from ..middleware.state_machine import ChatMode, set_chat_mode
    user, msg = update.effective_user, update.message
    if not user or user.id not in admin_user_ids or not msg:
        return
    arg = (context.args[0].lower() if context.args else "").strip()
    if arg == "on":
        set_client_view(user.id, True)
        set_chat_mode(user.id, ChatMode.AI_MODE)
        await msg.reply_text("客户视角已开启:现在起您发的消息按普通客户处理。发 /asclient off 退出,/asclient reset 清空测试痕迹。")
    elif arg == "off":
        set_client_view(user.id, False)
        set_draft_mode(user.id, False)
        await msg.reply_text("客户视角已关闭。")
    elif arg == "draft":
        set_client_view(user.id, True)
        set_draft_mode(user.id, True)
        set_chat_mode(user.id, ChatMode.AI_MODE)
        await msg.reply_text("客户视角(草稿模式)已开启:现在起您发的消息按普通客户处理,并使用您在设置中心里的<b>草稿</b>配置。/asclient off 退出。", parse_mode="HTML")
    elif arg == "reset":
        set_chat_mode(user.id, ChatMode.AI_MODE)
        counts = reset_client_state(user.id)
        await msg.reply_text("已清空测试痕迹:" + "、".join(f"{k} {v}" for k, v in counts.items()))
    else:
        state = "开启" if is_client_view(user.id) else "关闭"
        await msg.reply_text(f"客户视角当前:{state}\n用法:/asclient on | draft | off | reset")
