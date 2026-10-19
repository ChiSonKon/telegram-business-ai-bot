"""断点续接与回访判定(03 §3.3–3.4、§5.6)。

decide() 只做判定,不发消息:
- continue_silent:≤24 小时,按普通消息继续(问答中=作答/旁问;AI 阶段=进入 AI 回合)
- welcome_back_recent:24 小时–7 天,发"欢迎回来 + 上次摘要 + 继续/新问题"
- welcome_back_old:>7 天,发欢迎卡并在前面加一行"上次您咨询过:…"
- pending_status:人工待跟进中,发"处理中"卡(30 分钟节流)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from db.database import SessionMaker
from db.model import FrontdeskCase, FrontdeskMessage

from ...config import copy

STATUS_THROTTLE_MIN = 30
RECENT_HOURS = 24
OLD_DAYS = 7

REASON_LINE = {
    "support_escalation:payment": ("付款核对", "payment check"),
    "support_escalation:refund": ("退款", "refund"),
    "quote_request": ("报价", "your quote"),
    "large_deal": ("合作评估", "your enquiry"),
    "partner_intake": ("渠道合作", "partnership"),
    "explicit_human": ("您的咨询", "your request"),
    "sensitive_line": ("需求评估", "request review"),
}


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    from whitecat_core.runtime import now
    return now()


def _answers(case) -> dict:
    try:
        return json.loads(case.answers_json or "{}")
    except ValueError:
        return {}


def last_activity(user_id: int, case=None) -> datetime | None:
    """客户最近一次活动:最近一条消息(不含当前这条)与 resumed_at 取较晚者。"""
    with SessionMaker() as db:
        rows = db.query(FrontdeskMessage.created_at).filter(FrontdeskMessage.user_id == user_id).order_by(FrontdeskMessage.id.desc()).limit(2).all()
        c = case or db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=0).order_by(FrontdeskCase.id.desc()).first()
        resumed = _answers(c).get("resumed_at") if c else None
    prev = _aware(rows[1][0]) if len(rows) >= 2 else None
    if resumed:
        try:
            r = _aware(datetime.fromisoformat(resumed))
            prev = max(prev, r) if prev else r
        except ValueError:
            pass
    return prev


def decide(user_id: int, now: datetime | None = None, owner_id: int = 0) -> str | None:
    now = now or _now()
    with SessionMaker() as db:
        c = db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        if not c:
            return None
        state, role = c.state, c.role
    if state == "HUMAN_PENDING":
        return "pending_status"
    if state == "HUMAN_ACTIVE" or not role or state not in ("PRECHECK", "AI_SALES", "AI_SUPPORT", "AI_PARTNER"):
        return None
    prev = last_activity(user_id)
    if prev is None:
        return "continue_silent"
    gap = now - prev
    if gap <= timedelta(hours=RECENT_HOURS):
        return "continue_silent"
    if gap <= timedelta(days=OLD_DAYS):
        return "welcome_back_recent"
    return "welcome_back_old"


def summary_for_card(user_id: int, lang: str) -> str:
    """"业务线名 + 最多 30 字需求摘要",例如"定制机器人 · 自动回复客户"。"""
    from ...frontdesk import lines as biz_lines
    with SessionMaker() as db:
        c = db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=0).order_by(FrontdeskCase.id.desc()).first()
        a = _answers(c) if c else {}
        first = (c.initial_message if c else "") or ""
    line = a.get("line")
    ln = next((x for x in biz_lines.get_lines() if x["id"] == line), None)
    name = (ln.get("name_en" if lang == "en" else "name_zh") if ln else "") or ""
    need = (a.get("slots", {}).get("need") or a.get("slots", {}).get("message") or a.get("slots", {}).get("desc") or first or "").strip()
    need = " ".join(need.split())
    need = need if len(need) <= 30 else need[:29] + "…"
    return " · ".join(x for x in (name, need) if x) or ("上次的咨询" if lang == "zh" else "your last enquiry")


def mark_resumed(user_id: int, owner_id: int = 0) -> None:
    with SessionMaker() as db:
        c = db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        if c:
            a = _answers(c)
            a["resumed_at"] = _now().isoformat()
            c.answers_json = json.dumps(a, ensure_ascii=False)
            db.commit()


# ---------- 待跟进中的"处理中"卡 ----------
def fmt_wait(minutes: int, lang: str) -> str:
    if minutes < 1:
        return "不到 1 分钟" if lang == "zh" else "under a minute"
    if minutes < 60:
        return f"{minutes} 分钟" if lang == "zh" else f"{minutes} min"
    h, m = divmod(minutes, 60)
    if h < 24:
        return (f"{h} 小时 {m} 分" if lang == "zh" else f"{h} h {m} min") if m else (f"{h} 小时" if lang == "zh" else f"{h} h")
    return f"{h // 24} 天" if lang == "zh" else f"{h // 24} d"


def take_status_card(user_id: int, lang: str, owner_id: int = 0, force: bool = False) -> str | None:
    """需要发"处理中"卡时返回文案并记录时间;30 分钟内已发过返回 None(force=True 用于客户主动 /start)。"""
    now = _now()
    with SessionMaker() as db:
        c = db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        if not c or c.state != "HUMAN_PENDING":
            return None
        last = _aware(c.last_status_card_at)
        if last and now - last < timedelta(minutes=STATUS_THROTTLE_MIN) and not force:
            return None
        at = _aware(c.handoff_at) or _aware(c.updated_at) or now
        minutes = max(0, int((now - at).total_seconds() // 60))
        zh_en = REASON_LINE.get(c.handoff_reason or "")
        reason_line = (f"关于:{zh_en[0]}。" if lang == "zh" else f"About: {zh_en[1]}. ") if zh_en else ""
        c.last_status_card_at = now
        db.commit()
    return copy("pending_status", lang, reason_line=reason_line, wait=fmt_wait(minutes, lang))
