"""持久化前台规则：Bot 私聊与 Business 共用。所有额度和人工锁在 DB 中判定。"""
from __future__ import annotations
import json, os, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import func
from db.database import SessionMaker
from whitecat_core.runtime import epochs, epoch_key
from db.model import FrontdeskCase, AiUsageLedger, FrontdeskMessage, OrderVerification

POLICY_PATH = Path(__file__).resolve().parents[2] / "docs/frontdesk/policy.proposed.json"
POLICY = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
ROLE_LIMITS = {"prospect": POLICY["quotas"]["sales"]["session_reply_limit"], "support": POLICY["quotas"]["support"]["session_reply_limit"], "partner": POLICY["quotas"]["partner"]["session_reply_limit"], "visitor": 0}
END_TEXT = POLICY["copy"]["ai_ended"]

def get_case(user_id:int, channel:str="bot", owner_id:int=0, create=True):
    with SessionMaker() as db:
        case = db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        if not case and create:
            case=FrontdeskCase(user_id=user_id,business_owner_id=owner_id,channel=channel)
            db.add(case); db.commit(); db.refresh(case)
        return case.id if case else None

_KW = (
    ("support", ("售后","激活","安装失败","不到账","退款","订单","续费","授权异常","refund","activation","activate","not received","didn't arrive","my order","can't install","not working","error code")),
    ("partner", ("合作","代理","渠道","oem","供应","partner","reseller","cooperat","agency","white label")),
    ("visitor", ("留言","找人工","人工客服","找老板","看看","有哪些服务","介绍一下","talk to a human","real person","leave a message","just looking","browsing","what do you offer")),
    ("prospect", ("购买","买","价格","套餐","试用","软件","定制","报价","buy","price","pricing","plan","trial","custom","quote","software","how much","purchase")),
)

def route_intent(text:str)->str:
    t=(text or "").lower()
    for role,words in _KW:
        if any(x in t for x in words): return role
    return "uncertain"

def _ekey(user_id:int, owner_id:int=0)->str: return epoch_key(user_id,owner_id)

def set_intent(user_id:int, text:str, channel:str="bot", owner_id:int=0, role:str|None=None)->str:
    """role 显式给出时(按钮点击)直接采用;否则从文本推断。"""
    role=role or route_intent(text)
    with SessionMaker() as db:
        case=db.query(FrontdeskCase).filter_by(user_id=user_id,business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        if not case: case=FrontdeskCase(user_id=user_id,business_owner_id=owner_id,channel=channel,initial_message=text)
        if not case.initial_message: case.initial_message=text
        if role=="uncertain": case.role=None; case.state="FRONTDESK"; db.add(case); db.commit(); return role
        case.role=role; case.state={"prospect":"AI_SALES","support":"AI_SUPPORT","partner":"AI_PARTNER"}.get(role,"FRONTDESK")
        case.answers_json=json.dumps({"initial_message":case.initial_message},ensure_ascii=False)
        db.add(case); db.commit()
    return role

def is_human_locked(user_id:int, owner_id:int=0)->bool:
    with SessionMaker() as db:
        c=db.query(FrontdeskCase).filter_by(user_id=user_id,business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        return bool(c and c.state in ("HUMAN_PENDING","HUMAN_ACTIVE"))

def handoff(user_id:int, reason:str, owner_id:int=0, summary:str="", prio:str|None=None):
    with SessionMaker() as db:
        c=db.query(FrontdeskCase).filter_by(user_id=user_id,business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        if not c: c=FrontdeskCase(user_id=user_id,business_owner_id=owner_id)
        from ..outbox import reason_info
        prio=prio or reason_info(reason)[0]
        c.state="HUMAN_PENDING"; c.handoff_reason=reason; c.summary=summary or c.summary; c.handoff_at=datetime.now(timezone.utc); c.priority=prio
        db.add(c); db.commit(); db.refresh(c)
        cid=c.id; note=c.summary or ""
    try:
        from ..analytics.events import emit
        emit("handoff", user_id, cid, reason=reason[:32] if not reason.startswith("guardrail") else "guardrail", prio=prio)
    except Exception: pass
    epochs.bump(_ekey(user_id,owner_id))     # 状态已落库,再让在途 AI 任务失效
    try:                                     # 通知管理群:先入库再投递,管理群不可达也不丢
        from ..outbox import enqueue_admin, t_admin_handoff
        from .intake import brief as brief_mod
        btxt=""
        try:
            b=brief_mod.load(user_id,owner_id)
            if summary and not b.first_message: b.first_message=summary[:200]
            btxt=brief_mod.render_for_admin(b)
        except Exception: pass
        enqueue_admin(f"handoff:{cid}:{reason}", t_admin_handoff(user_id, reason, note, btxt, prio), user_id)
    except Exception: pass
    return cid

def record_human_reply(user_id:int, owner_id:int=0):
    """同事首次回复客户:记 human_first_reply(等待分钟与优先级),每次转交只记一次。"""
    try:
        with SessionMaker() as db:
            c=db.query(FrontdeskCase).filter_by(user_id=user_id,business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
            if not c or not c.handoff_at: return
            try: a=json.loads(c.answers_json or "{}")
            except ValueError: a={}
            if a.get("human_replied_for")==c.handoff_at.isoformat(): return
            a["human_replied_for"]=c.handoff_at.isoformat(); c.answers_json=json.dumps(a,ensure_ascii=False)
            at=c.handoff_at if c.handoff_at.tzinfo else c.handoff_at.replace(tzinfo=timezone.utc)
            from whitecat_core.runtime import now
            wait=max(0,int((now()-at).total_seconds()//60)); prio=c.priority or "P2"; cid=c.id
            db.commit()
        from ..analytics.events import emit
        emit("human_first_reply", user_id, cid, wait_min=wait, prio=prio)
    except Exception: pass

def admin_takeover(user_id:int, owner_id:int=0):
    with SessionMaker() as db:
        c=db.query(FrontdeskCase).filter_by(user_id=user_id,business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        if c: c.state="HUMAN_ACTIVE"; db.commit()
    epochs.bump(_ekey(user_id,owner_id))

def admin_release(user_id:int, owner_id:int=0):
    with SessionMaker() as db:
        c=db.query(FrontdeskCase).filter_by(user_id=user_id,business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        if c and c.state=="HUMAN_ACTIVE": c.state="FRONTDESK"; db.commit()

def record_message(user_id:int, direction:str, text:str, channel="bot", message_id=None, case_id=None):
    with SessionMaker() as db:
        db.add(FrontdeskMessage(user_id=user_id,case_id=case_id or get_case(user_id,channel),channel=channel,direction=direction,text=text[:12000],message_id=message_id)); db.commit()

def reserve_reply(user_id:int, role:str, case_id:int=None, owner_id:int=0):
    key=uuid.uuid4().hex
    window=datetime.now(timezone.utc)-timedelta(hours=POLICY["quotas"]["rolling_window_hours"])
    with SessionMaker() as db:
        case=db.get(FrontdeskCase,case_id) if case_id else db.query(FrontdeskCase).filter_by(user_id=user_id,business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        if not case or is_human_locked(user_id,owner_id): return None
        role_limit=ROLE_LIMITS.get(role,0)
        used=db.query(func.coalesce(func.sum(AiUsageLedger.reply_units),0)).filter(AiUsageLedger.user_id==user_id,AiUsageLedger.role==role,AiUsageLedger.event.in_(("success","reserve","grant")),AiUsageLedger.created_at>=window).scalar() or 0
        all_used=db.query(func.coalesce(func.sum(AiUsageLedger.reply_units),0)).filter(AiUsageLedger.user_id==user_id,AiUsageLedger.event.in_(("success","reserve","grant")),AiUsageLedger.created_at>=window).scalar() or 0
        if used >= role_limit or all_used >= POLICY["quotas"]["all_roles_rolling_reply_limit"]: return None
        db.add(AiUsageLedger(user_id=user_id,business_owner_id=owner_id,case_id=case.id,role=role,event="reserve",reply_units=1,reservation_key=key)); db.commit()
    return key

def settle_reply(key:str, success:bool, attempts:int=1, input_tokens:int=0, output_tokens:int=0, cost_usd:float=0, estimated:bool=False):
    with SessionMaker() as db:
        r=db.query(AiUsageLedger).filter_by(reservation_key=key).first()
        if not r: return False
        r.event="success" if success else "release"; r.model_attempts=attempts; r.input_tokens=input_tokens; r.output_tokens=output_tokens; r.cost_usd=cost_usd; r.estimated=estimated; db.commit(); return True

def can_send_ai(user_id:int, owner_id:int=0)->bool: return not is_human_locked(user_id,owner_id)

def confirm_payment(user_id:int, order_ref:str, verifier:str, event_id:str, case_id=None):
    if not verifier or not event_id: raise ValueError("可信核验人和唯一事件号必填")
    with SessionMaker() as db:
        if db.query(OrderVerification).filter_by(unique_event_id=event_id).first(): return False
        db.add(OrderVerification(user_id=user_id,case_id=case_id,order_ref=order_ref,status="paid",evidence_type="verified",verified_by=verifier,verified_at=datetime.now(timezone.utc),unique_event_id=event_id)); db.commit(); return True


def get_role(user_id:int, owner_id:int=0)->str|None:
    with SessionMaker() as db:
        c=db.query(FrontdeskCase).filter_by(user_id=user_id,business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        return c.role if c else None

def remaining_replies(user_id:int, role:str, owner_id:int=0)->int:
    """会话/滚动窗口内该角色剩余成功回复数(用于临近上限的温和提示)。"""
    window=datetime.now(timezone.utc)-timedelta(hours=POLICY["quotas"]["rolling_window_hours"])
    with SessionMaker() as db:
        used=db.query(func.coalesce(func.sum(AiUsageLedger.reply_units),0)).filter(AiUsageLedger.user_id==user_id,AiUsageLedger.role==role,AiUsageLedger.event.in_(("success","reserve","grant")),AiUsageLedger.created_at>=window).scalar() or 0
    return max(0,ROLE_LIMITS.get(role,0)-used)

def idle_hours_before_current(user_id:int)->float|None:
    """当前消息(已记录)之前,该客户最近一条消息距今小时数;没有则 None。"""
    with SessionMaker() as db:
        rows=db.query(FrontdeskMessage.created_at).filter(FrontdeskMessage.user_id==user_id).order_by(FrontdeskMessage.id.desc()).limit(2).all()
    if len(rows)<2 or rows[1][0] is None: return None
    prev=rows[1][0]
    from whitecat_core.runtime import now as _now
    now=_now() if prev.tzinfo else _now().replace(tzinfo=None)
    return (now-prev).total_seconds()/3600

def in_precheck(user_id:int, owner_id:int=0)->bool:
    with SessionMaker() as db:
        c=db.query(FrontdeskCase).filter_by(user_id=user_id,business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        return bool(c and c.state=="PRECHECK")

def case_state(user_id:int, owner_id:int=0)->str|None:
    with SessionMaker() as db:
        c=db.query(FrontdeskCase).filter_by(user_id=user_id,business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        return c.state if c else None

def take_flag(user_id:int, key:str, owner_id:int=0)->bool:
    """在 case.answers_json 里设置一次性标记;首次设置返回 True,已存在返回 False。"""
    with SessionMaker() as db:
        c=db.query(FrontdeskCase).filter_by(user_id=user_id,business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        if not c: return False
        try: a=json.loads(c.answers_json or "{}")
        except ValueError: a={}
        if a.get(key): return False
        a[key]=True; c.answers_json=json.dumps(a,ensure_ascii=False); db.commit(); return True
