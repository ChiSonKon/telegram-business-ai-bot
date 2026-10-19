"""管理群可靠投递:先入库、后发送、失败退避重试、按 dedupe_key 去重。

交接通知带"优先级 + 原因 + 预检摘要 + 一键处理"按钮,管理员不必翻聊天记录。
"""
from __future__ import annotations

import html
import logging
import time

from sqlalchemy.exc import IntegrityError

from db.database import SessionMaker
from db.model import Outbox, User

from .global_settings import get_admin_group_id
from .ui import btn, kb, send_card

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 50

# 原因 -> (优先级, 说明)。P0 付款/争议,P1 明确交易意向或系统故障,P2 一般咨询,P3 纯留言
REASONS = {
    "support_escalation:payment": ("P0", "付款未到账"),
    "support_escalation:refund": ("P0", "退款/争议"),
    "quota_exhausted": ("P1", "AI 额度已用完（客户还有话要说）"),
    "system_unavailable": ("P1", "AI 暂时不可用"),
    "budget_exhausted": ("P1", "全局 AI 预算已用完"),
    "partner_intake": ("P1", "合作意向"),
    "explicit_human": ("P2", "客户主动要求对接同事"),
    "visitor_message": ("P3", "客户留言"),
    "no_role": ("P2", "尚未选择来意"),
    "ai_silent": ("P2", "AI 首轮未能回复(已告知客户转同事)"),
    "sensitive_line": ("P2", "敏感业务登记(只登记,是否承接由同事评估)"),
    "large_deal": ("P1", "大额/企业客户"),
    "quote_request": ("P1", "请求报价"),
    "refund": ("P0", "退款/争议"),
    "paid_but_broken": ("P0", "已付款客户的卡密/授权问题"),
    "angry_paid_customer": ("P0", "已付款客户情绪激动"),
    "ai_stuck": ("P2", "AI 连续两轮未能解决客户问题"),
    "partner_intake": ("P1", "合作意向"),
}


def reason_info(reason: str) -> tuple[str, str]:
    if reason.startswith("guardrail"):
        return "P1", "AI 回复涉及金额/链接/地址,已被拦截,请核实后回复"
    return REASONS.get(reason, ("P2", reason))


def t_admin_handoff(user_id: int, reason: str, note: str = "", brief_text: str = "", prio: str | None = None) -> str:
    base_prio, label = reason_info(reason)
    prio = prio or base_prio
    out = (f"[[alert]] <b>{prio} · 需要跟进</b>\n客户 <a href=\"tg://user?id={user_id}\">{user_id}</a>\n原因：{label}")
    if brief_text:
        out += f"\n<b>接待简报</b>\n{html.escape(brief_text[:900])}"
    elif note:
        out += f"\n预检摘要：{html.escape(note[:600])}"
    return out


def enqueue_admin(dedupe_key: str, text: str, user_id: int | None = None, chat_id: int | None = None) -> bool:
    """入队一条通知(默认发往管理群并定位到客户话题、附带处理按钮;指定 chat_id 则私信该管理员)。已存在同 key 返回 False。"""
    with SessionMaker() as db:
        thread = None
        if user_id is not None and chat_id is None:
            u = db.query(User).filter(User.user_id == user_id).first()
            thread = u.message_thread_id if u else None
        db.add(Outbox(dedupe_key=dedupe_key, chat_id=chat_id if chat_id is not None else get_admin_group_id(), thread_id=thread, text=text,
                      case_uid=user_id if chat_id is None else None))
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False


def _case_button(bot, case_uid: int):
    """话题里的"打开处理卡":用深链按钮直接跳转到机器人私聊(/start case_<uid>),而不是弹窗提醒。拿不到机器人用户名时退回回调按钮。"""
    from . import bot_username as env_username
    username = (getattr(bot, "username", None) or env_username or "").lstrip("@")
    if username:
        return kb([[btn("打开处理卡", icon="folder", style="primary", url=f"https://t.me/{username}?start=case_{case_uid}")]])
    return kb([[btn("打开处理卡", icon="folder", style="primary", callback_data=f"adm:case:{case_uid}")]])


async def drain(bot, limit: int = 20, clock=time.time) -> int:
    """发送到期的未发送项;返回成功数。send_card 内部吞掉异常并返回 None,以此判定失败。"""
    sent = 0
    with SessionMaker() as db:
        rows = db.query(Outbox).filter(Outbox.sent.is_(False), Outbox.next_try <= clock(), Outbox.attempts < MAX_ATTEMPTS) \
                 .order_by(Outbox.id).limit(limit).all()
        ids = [r.id for r in rows]
    for oid in ids:
        with SessionMaker() as db:
            r = db.get(Outbox, oid)
            extra = {"message_thread_id": r.thread_id} if r.thread_id else {}
            markup = _case_button(bot, r.case_uid) if r.case_uid else None
            msg = await send_card(bot, r.chat_id, r.text, markup, None, **extra)
            if msg is None and r.thread_id:      # 话题被删等:退回主群发送,避免永远卡住
                msg = await send_card(bot, r.chat_id, r.text, markup, None)
            if msg is not None:
                r.sent = True
                sent += 1
            else:
                r.attempts += 1
                r.next_try = clock() + min(2 ** r.attempts * 10, 3600)
            db.commit()
    return sent
