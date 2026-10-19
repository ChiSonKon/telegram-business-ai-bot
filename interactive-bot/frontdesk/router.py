"""前台路由(WP1):先答后问。Bot 私聊与 Business 共用。

流程:预检中 → 只作用于预检;AI 阶段 → 沿用既有逻辑;其余(新客户/先答后/回访)→ 入口分类器 → 先答卡或进入问答。
所有先答卡零模型调用;答完后 case.state=ANSWERED,下一句带着上次的业务线继续。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from db.database import SessionMaker
from db.model import AppSetting, FrontdeskCase, Order
from whitecat_core.runtime import lang_for

from ..config import copy
from ..ui import btn, kb, send_card
from . import answers, entry, escalation, lines as biz_lines, precheck, trust
from .intake import brief as brief_mod, engine, extract_rules, resume
from ..experience import cleanup, ui_registry
from .service import (case_state, get_role, handoff, idle_hours_before_current, in_precheck, is_human_locked, set_intent)
from .texts import t

logger = logging.getLogger(__name__)

RETURNING_AFTER_HOURS = 24      # 超过此间隔再来的客户重新走入口(额度按滚动窗口计,不会因此重置)
_LEAVE_MSG = ("留言", "找人工", "转人工", "人工客服", "找真人", "找老板", "叫人", "找主理人", "leave a message", "talk to a human", "real person",
              "human agent", "speak to someone", "talk to someone")
_FRUSTRATED = ("听不懂", "别扯了", "骗子", "不买了", "差评", "this is useless", "scam", "not helpful", "you don't understand")
_PICK_WORDS = ("帮我选", "选哪个", "哪个套餐", "哪个好", "推荐", "which plan", "help me choose", "recommend")
_OPEN_ORDER = ("quoted", "awaiting_payment", "payment_review")
_PAID_ORDER = ("paid", "fulfilling", "delivered")


@dataclass
class Gate:
    ai: bool = False                  # True = 可以进入 AI 回合
    texts: list[str] = field(default_factory=list)
    immediate: bool = False           # True = 预检刚完成,立即用 texts 发起首轮,不走防抖
    care: bool = False                # True = 客户表达不满,已发出可选择对接同事的卡片


def classify(text: str) -> str | None:
    """'human'(明确要对接同事/留言) / 'frustrated'(不满) / None。"""
    low = (text or "").lower()
    if any(k in low for k in _LEAVE_MSG):
        return "human"
    if any(k in low for k in _FRUSTRATED):
        return "frustrated"
    return None


def _emit(event: str, user_id: int, **props) -> None:
    try:
        from ..analytics.events import emit
    except ImportError:
        return
    emit(event, user_id, **props)


# ---------- 发送(含 Business 披露) ----------
def _disclosure_key(user_id: int) -> str:
    return f"biz_disclosed:{user_id}"


def take_disclosure(user_id: int, biz_conn_id: str | None) -> bool:
    """Business 会话中机器人的第一条消息需要披露 AI 身份。首次调用返回 True 并记录。"""
    if not biz_conn_id:
        return False
    from . import profile
    return profile.take_disclosure(user_id)


async def send(bot, user_id: int, chat_id: int, text: str, markup=None, biz_conn_id: str | None = None, lang: str = "zh"):
    """发卡片;Business 首条消息前加披露行。"""
    if take_disclosure(user_id, biz_conn_id):
        text = copy("biz.disclosure", lang) + "\n\n" + text
    return await send_card(bot, chat_id, text, markup, biz_conn_id)


# ---------- advance:沿用预检推进 ----------
async def advance(bot, user_id: int, chat_id: int, lang: str, biz_conn_id: str | None = None, edit=None) -> Gate:
    """向前推进预检:还有问题就发下一张卡;收齐则出确认卡并(视流程)交给 AI 或同事。
    edit: 回调场景传入的"替换当前卡片"函数 async (text, markup) -> None。"""
    async def show(text, markup, track=False):
        if edit:
            await edit(text, markup)
            return
        msg = await send(bot, user_id, chat_id, text, markup, biz_conn_id, lang)
        if track and msg is not None and getattr(msg, "message_id", None):           # 进度卡登记:新卡发出后,旧进度卡去掉按钮
            ui_id = ui_registry.record(user_id, chat_id, msg.message_id, "intake", biz=biz_conn_id)
            await cleanup.retire_previous(bot, user_id, "intake", keep_id=ui_id)

    nxt = precheck.prompt(user_id, lang)
    if nxt:
        await show(*nxt, track=True)
        _emit("intake_step_shown", user_id, slot=precheck.current_step(user_id) or "-")
        return Gate()
    if True:
        notice = engine.pending_notice(user_id, lang)
        if notice:
            await send(bot, user_id, chat_id, notice, None, biz_conn_id, lang)
        conf = engine.confirm_card(user_id, lang)
        if conf:
            await show(*conf, track=True)
            return Gate()
    done = precheck.complete(user_id, lang)
    if not done:
        return Gate()
    if done.text:
        await show(done.text, None)
    if getattr(done, "collapsed", ""):                                               # 问答结束:进度卡收起为一行"✓ 需求已记下"
        await cleanup.collapse_latest(bot, user_id, "intake", done.collapsed)
    if done.kind == "ai":
        return Gate(ai=True, texts=[done.query], immediate=True)
    handoff(user_id, done.reason, summary=done.summary, prio=getattr(done, "prio", None))
    return Gate()


# ---------- case 辅助 ----------
def _latest_case(db, user_id: int, owner_id: int = 0):
    return db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()


def _answers(case) -> dict:
    try:
        return json.loads(case.answers_json or "{}")
    except ValueError:
        return {}


def case_line_hint(user_id: int) -> str | None:
    """ANSWERED 状态下,上一张先答卡的业务线(用于解释"帮我选/多少钱"等省略句)。"""
    with SessionMaker() as db:
        c = _latest_case(db, user_id)
        if c and c.state == "ANSWERED":
            return _answers(c).get("line")
    return None


def mark_answered(user_id: int, channel: str, text: str = "", line: str | None = None, card: str | None = None) -> None:
    with SessionMaker() as db:
        c = _latest_case(db, user_id)
        if not c:
            c = FrontdeskCase(user_id=user_id, business_owner_id=0, channel=channel, initial_message=text)
            db.add(c)
        if c.state in ("HUMAN_PENDING", "HUMAN_ACTIVE", "PRECHECK"):
            db.commit()
            return
        a = _answers(c)
        if line:
            a["line"] = line
        if card:
            a["last_card"] = card
        if not c.initial_message:
            c.initial_message = text
        c.state, c.role = "ANSWERED", None
        c.answers_json = json.dumps(a, ensure_ascii=False)
        db.commit()


def set_source(user_id: int, source: str, channel: str = "bot") -> None:
    with SessionMaker() as db:
        c = _latest_case(db, user_id)
        if not c:
            c = FrontdeskCase(user_id=user_id, business_owner_id=0, channel=channel, state="FRONTDESK")
            db.add(c)
        a = _answers(c)
        a["source"] = source
        c.source = source[:64]
        from . import profile
        profile.note_source(user_id, source)
        c.answers_json = json.dumps(a, ensure_ascii=False)
        db.commit()


def mark_bot_started(user_id: int) -> None:
    from . import profile
    profile.mark_bot_started(user_id)


def case_source(user_id: int) -> str | None:
    with SessionMaker() as db:
        c = _latest_case(db, user_id)
        return _answers(c).get("source") if c else None


def _orders(user_id: int) -> tuple[bool, bool]:
    with SessionMaker() as db:
        states = {s for (s,) in db.query(Order.state).filter(Order.user_id == user_id).all()}
    return bool(states & set(_OPEN_ORDER)), bool(states & set(_PAID_ORDER))


def _line_name(line_id: str, lang: str) -> str:
    ln = next((x for x in biz_lines.get_lines() if x["id"] == line_id), None)
    if not ln:
        return line_id
    return ln.get("name_en" if lang == "en" else "name_zh") or ln.get("name_zh") or line_id


def _latest_paid_order(user_id: int):
    with SessionMaker() as db:
        o = db.query(Order).filter(Order.user_id == user_id, Order.state.in_(_PAID_ORDER)).order_by(Order.updated_at.desc()).first()
        return (o.id, trust._amount(o), o.updated_at) if o else None


# ---------- 升级矩阵(WP4) ----------
def _esc_pending(user_id: int) -> dict | None:
    with SessionMaker() as db:
        c = _latest_case(db, user_id)
        return _answers(c).get("esc_pending") if c else None


def _esc_set(user_id: int, data: dict | None, channel: str = "bot") -> None:
    with SessionMaker() as db:
        c = _latest_case(db, user_id)
        if not c:
            c = FrontdeskCase(user_id=user_id, business_owner_id=0, channel=channel, state="FRONTDESK")
            db.add(c)
            db.flush()
        a = _answers(c)
        if data is None:
            a.pop("esc_pending", None)
        else:
            a["esc_pending"] = data
        c.answers_json = json.dumps(a, ensure_ascii=False)
        db.commit()


def _counter(user_id: int, key: str, reset: bool = False, inc: bool = False) -> int:
    with SessionMaker() as db:
        c = _latest_case(db, user_id)
        if not c:
            return 0
        a = _answers(c)
        n = 0 if reset else a.get(key, 0) + (1 if inc else 0)
        if n != a.get(key, 0):
            a[key] = n
            c.answers_json = json.dumps(a, ensure_ascii=False)
            db.commit()
        return n


def _card_vars(user_id: int, prio: str, lang: str) -> dict:
    order = _latest_paid_order(user_id)
    v = {"eta": escalation.eta_text(prio, lang), "id": "-", "amount": "-", "paid_at": "-"}
    if order:
        oid, (amount, cur), paid_at = order
        v.update(id=oid, amount=amount, paid_at=paid_at.strftime("%m-%d %H:%M") if paid_at else "-", currency=cur)
    return v


def escalation_card(rule_card: str, prio: str, user_id: int, lang: str) -> str:
    v = _card_vars(user_id, prio, lang)
    text = copy(rule_card, lang, **{k: v[k] for k in ("eta", "id", "amount", "paid_at")})
    if rule_card == "care.angry_paid" and v.get("currency") not in (None, "USDT"):
        text = text.replace("USDT", v["currency"])
    return text.strip()


async def run_escalation(bot, user_id, chat_id, esc, text, lang, biz_conn_id, channel) -> Gate:
    """命中升级规则:需要先问一句就先问(客户下一条消息即为描述),否则直接转交。"""
    if esc.ask_first:
        _esc_set(user_id, {"rule": esc.rule_id, "reason": esc.reason, "prio": esc.prio, "card": esc.card, "orig": text[:200]}, channel)
        await send(bot, user_id, chat_id, copy(esc.ask_first, lang), None, biz_conn_id, lang)
        return Gate()
    return await finish_escalation(bot, user_id, chat_id, {"rule": esc.rule_id, "reason": esc.reason, "prio": esc.prio, "card": esc.card, "orig": ""},
                                   text, lang, biz_conn_id, channel)


async def finish_escalation(bot, user_id, chat_id, pend: dict, text: str, lang, biz_conn_id, channel) -> Gate:
    _esc_set(user_id, None, channel)
    summary = (pend.get("orig", "") + (" → " if pend.get("orig") and text else "") + text)[:300]
    card = escalation_card(pend["card"], pend["prio"], user_id, lang)
    handoff(user_id, pend["reason"], summary=summary, prio=pend["prio"])
    await send(bot, user_id, chat_id, card, None, biz_conn_id, lang)
    return Gate()


def _intake_slots(user_id: int) -> tuple[dict, dict, str | None]:
    b = brief_mod.load(user_id)
    return b.slots, b.signals, b.line


# ---------- 欢迎卡 ----------
def welcome_markup(source: str | None, lang: str):
    rows = [
        [btn(copy("btn.software", lang), icon="shop", style="primary", callback_data="fd:go:software")],
        [btn(copy("btn.custom", lang), icon="robot", style="primary", callback_data="fd:go:bot_custom")],
        [btn(copy("btn.more", lang), icon="globe", callback_data="fd:menu:more")],
        [btn(copy("btn.support", lang), icon="gear", callback_data="fd:role:support")],
        [btn(copy("btn.partner", lang), icon="briefcase", callback_data="fd:role:partner")],
    ]
    if source in ("src_custom", "custom"):                  # 来源感知:定制来源把定制放最前
        rows[0], rows[1] = rows[1], rows[0]
    return kb(rows)


def more_markup(lang: str):
    return kb([
        [btn(copy("btn.more.network", lang), icon="globe", callback_data="fd:go:network")],
        [btn(copy("btn.more.ai", lang), icon="brain", callback_data="fd:go:ai_account")],
        [btn(copy("btn.more.other", lang), icon="chat", callback_data="fd:go:other_biz")],
    ])


async def _send_welcome(bot, user_id, chat_id, lang, biz_conn_id, channel, returning=False):
    if biz_conn_id:                                          # Business:不发菜单卡(03 §4.6)
        with SessionMaker() as db:
            known = bool(_latest_case(db, user_id)) or any(_orders(user_id))
        key = "biz.greeting_known" if known else "biz.greeting_new"
        await send(bot, user_id, chat_id, copy(key, lang), None, biz_conn_id, lang)
        return
    card = t("welcome_back", lang) if returning else copy("welcome", lang)      # WP3 起由 resume.decide 接管回访卡
    msg = await send(bot, user_id, chat_id, card, welcome_markup(case_source(user_id), lang), biz_conn_id, lang)
    if msg is not None and getattr(msg, "message_id", None):
        ui_id = ui_registry.record(user_id, chat_id, msg.message_id, "welcome", biz=biz_conn_id)
        await cleanup.supersede_welcome(bot, user_id, keep_id=ui_id)


# ---------- 业务线先答 ----------
async def answer_line(bot, user_id, chat_id, line: str, text: str, lang, biz_conn_id, channel, *, edit=None) -> Gate:
    """说明卡 + 进入问答(bot_custom/network/ai_account/other_biz)。"""
    intro = answers.LINE_INTRO.get(line)
    if intro:
        card, markup = intro(lang)
        if edit:
            await edit(card, markup)
        else:
            await send(bot, user_id, chat_id, card, markup, biz_conn_id, lang)
        _emit("answer_card_shown", user_id, card=f"{line}_intro")
    precheck.begin(user_id, "prospect", text, channel=channel, line=line)
    await _llm_prefill(user_id, text)
    if edit and intro:                                       # 说明卡已占用原卡片,问题另发新卡
        return await advance(bot, user_id, chat_id, lang, biz_conn_id)
    return await advance(bot, user_id, chat_id, lang, biz_conn_id, edit=edit if not intro else None)


async def _llm_prefill(user_id: int, text: str) -> None:
    """可选的模型抽取(默认关闭):首句较长且必填槽位缺失时,让模型补填一次。"""
    from .intake import extract_llm
    if not (text and extract_llm.enabled()):
        return
    try:
        from ..ai_turn import shared
        await extract_llm.maybe_extract(user_id, text, llm=shared()[0])
    except Exception:
        logger.exception("llm prefill failed")


def _record_software_brief(user_id: int, text: str, channel: str) -> None:
    """把首句里能抽到的系统/设备数/时长写入简报,供 AI 与同事使用(一句五问:G-13)。"""
    ex = extract_rules.extract_all(text)
    slots = {k: ex[k] for k in ("os", "horizon") if k in ex}
    if "devices" in ex:
        slots["devices"] = engine._ex_devices(text, ex, {}, {})
    sig = {k: ex[k] for k in ("urgency", "sentiment", "trust_concern", "company", "reseller", "questions") if k in ex}
    brief_mod.update(user_id, role="prospect", line="software", channel=channel, slots=slots, signals=sig, first_message=text[:300])


async def software_to_ai(bot, user_id, chat_id, text: str, lang, biz_conn_id, channel, *, ack=True) -> Gate:
    if ack:
        n = extract_rules.questions(text)
        card = copy("software_ai_ack_multi", lang, n=n) if n >= 3 else copy("software_ai_ack", lang)
        await send(bot, user_id, chat_id, card, None, biz_conn_id, lang)
    set_intent(user_id, text, channel=channel, role="prospect")
    with SessionMaker() as db:
        c = _latest_case(db, user_id)
        a = _answers(c)
        a["line"] = "software"
        c.answers_json = json.dumps(a, ensure_ascii=False)
        db.commit()
    _record_software_brief(user_id, text, channel)
    return Gate(ai=True, texts=[text])


# ---------- 主入口 ----------
def touch_customer(user_id: int) -> None:
    """记录客户最近一次发言时间(intake 放弃判定、统计用)。"""
    with SessionMaker() as db:
        c = _latest_case(db, user_id)
        if c:
            c.last_customer_at = runtime_now()
            db.commit()


def runtime_now():
    from whitecat_core.runtime import now
    return now()


def _maybe_ai_effective(user_id: int, text: str) -> None:
    """AI 回复后 30 分钟内客户继续回应(≥2 字)= 一次有效对话;每个 AI 回合最多记一次。"""
    if len((text or "").strip()) < 2:
        return
    from db.model import FrontdeskMessage
    with SessionMaker() as db:
        rows = db.query(FrontdeskMessage).filter(FrontdeskMessage.user_id == user_id).order_by(FrontdeskMessage.id.desc()).limit(2).all()
        if len(rows) < 2 or rows[1].direction != "out" or rows[1].created_at is None:
            return
        prev = rows[1]
        now = runtime_now()
        prev_at = prev.created_at if prev.created_at.tzinfo else prev.created_at.replace(tzinfo=timezone.utc)
        if (now - prev_at).total_seconds() > 30 * 60:
            return
        c = _latest_case(db, user_id)
        if not c:
            return
        a = _answers(c)
        if a.get("eff_mark") == prev.id:
            return
        a["eff_mark"] = prev.id
        c.answers_json = json.dumps(a, ensure_ascii=False)
        db.commit()
        turn_no = db.query(FrontdeskMessage).filter(FrontdeskMessage.user_id == user_id, FrontdeskMessage.direction == "out").count()
    _emit("ai_effective", user_id, turn_no=turn_no)


async def handle_customer_text(bot, user_id: int, chat_id: int, text: str, *, channel: str, biz_conn_id: str | None = None) -> Gate:
    from ..admin import sandbox
    from ..config import draft_scope
    with draft_scope(sandbox.scope_user(user_id)):
        return await _handle_customer_text(bot, user_id, chat_id, text, channel=channel, biz_conn_id=biz_conn_id)


async def _handle_customer_text(bot, user_id: int, chat_id: int, text: str, *, channel: str, biz_conn_id: str | None = None) -> Gate:
    lang = lang_for(user_id, text)
    touch_customer(user_id)

    # 0) 回访判定(03 §3.3/§5.6):24h 内静默续接;24h–7 天发"欢迎回来"卡;>7 天发带摘要的欢迎卡
    decision = resume.decide(user_id)
    if decision == "pending_status":
        card = resume.take_status_card(user_id, lang)
        if card:
            await send(bot, user_id, chat_id, card, kb([[btn(copy("btn.different_question", lang), callback_data="fd:newcase")]]), biz_conn_id, lang)
        return Gate()
    if decision == "welcome_back_recent":
        _emit("returned", user_id, action="card", gap_hours=int(min(9999, (runtime_now() - (resume.last_activity(user_id) or runtime_now())).total_seconds() // 3600)))
        card = copy("welcome_back_recent", lang, summary=resume.summary_for_card(user_id, lang))
        markup = kb([[btn(copy("btn.continue", lang), style="primary", callback_data="fd:resume:continue")],
                     [btn(copy("btn.new_topic", lang), callback_data="fd:resume:new")]])
        await send(bot, user_id, chat_id, card, markup, biz_conn_id, lang)
        return Gate()
    if decision == "welcome_back_old":
        prefix = copy("welcome_back_old_prefix", lang, summary=resume.summary_for_card(user_id, lang))
        set_intent(user_id, text, channel=channel, role="uncertain")
        await send(bot, user_id, chat_id, prefix + copy("welcome", lang), welcome_markup(case_source(user_id), lang), biz_conn_id, lang)
        return Gate()

    # 0.5) 升级矩阵"先问一句"之后的回答:这条消息就是客户的描述 → 转交(客户说"算了"则取消)
    pend = _esc_pending(user_id)
    if pend:
        if escalation.is_cancel(text):
            _esc_set(user_id, None, channel)
            await send(bot, user_id, chat_id, copy("human.cancelled", lang), None, biz_conn_id, lang)
            return Gate()
        return await finish_escalation(bot, user_id, chat_id, pend, text, lang, biz_conn_id, channel)

    role = get_role(user_id)
    returning = bool(role) and case_state(user_id) == "RESOLVED"
    precheck_now = in_precheck(user_id)
    ai_stage = bool(role) and not returning and not precheck_now
    open_order, paid_order = _orders(user_id)
    hint = case_line_hint(user_id)
    intent = entry.classify_entry(text, has_open_order=open_order, has_paid_order=paid_order, line_hint=hint)
    if intent.kind == "unknown" and hint and role is None and any(w in text.lower() for w in _PICK_WORDS):
        intent = entry.EntryIntent("pick", line=hint)

    # AI 阶段的不满计数(连续 2 条"没用/不对/听不懂") 与 愤怒但无订单:安抚并继续,连续第二次 → ai_stuck
    complaints = 0
    if ai_stage:
        if entry._has(text.lower(), entry._kw("dissatisfied")):
            complaints = _counter(user_id, "complaints", inc=True)
        else:
            _counter(user_id, "complaints", reset=True)
    if intent.signals.get("angry") and not paid_order:
        if _counter(user_id, "angry_n", inc=True) >= 2:
            complaints = max(complaints, 2)
        else:
            await send(bot, user_id, chat_id, copy("care.angry", lang), None, biz_conn_id, lang)
    elif not intent.signals.get("angry"):
        _counter(user_id, "angry_n", reset=True)

    # 升级矩阵:命中即跳过 AI(最多先问一句)
    slots_now, signals_now, line_now = _intake_slots(user_id)
    esc = escalation.evaluate(escalation.Ctx(
        intent=intent.kind, text=text, ex=extract_rules.extract_all(text), line=intent.line or line_now or hint, slots=slots_now,
        signals=signals_now, has_open_order=open_order, has_paid_order=paid_order, complaints=complaints, in_ai_stage=ai_stage))
    if esc:
        _emit("entry", user_id, intent=intent.kind)
        return await run_escalation(bot, user_id, chat_id, esc, text, lang, biz_conn_id, channel)

    # 1) 预检进行中:消息只作用于预检,绝不唤起 AI
    if precheck_now:
        from . import interests
        hit = interests.detect(text, interests.primary_line_id(user_id))       # 问答中途客户又提到别的业务:登记为并行意向并衔接一句,问答照常继续
        if hit and not (hit.get("sensitive")) and interests.register(user_id, hit, text):
            await send(bot, user_id, chat_id, copy("multi.noted", lang, name=interests._name(hit, lang)), None, biz_conn_id, lang)
        res = precheck.accept_text(user_id, text, lang=lang)
        if res is not None and getattr(res, "side_reply", None):
            await send(bot, user_id, chat_id, res.side_reply, None, biz_conn_id, lang)
        return await advance(bot, user_id, chat_id, lang, biz_conn_id)

    # 2) AI 阶段
    if ai_stage:
        _maybe_ai_effective(user_id, text)
        return Gate(ai=True, texts=[text])

    # 3) 入口分类
    _emit("entry", user_id, intent=intent.kind)
    kind = intent.kind
    first_value = lambda card: _emit("first_value", user_id, card=card)

    if kind == "verify_request":
        v = trust.verify(text, lang)
        await send(bot, user_id, chat_id, v.text, None, biz_conn_id, lang)
        _emit("verify_check", user_id, kind="account", official=not v.unofficial)
        if v.unofficial:
            trust.report_impersonation(user_id, v, text)
            _emit("impersonation_report", user_id)
        mark_answered(user_id, channel, text, card="verify")
        first_value("verify")
        return Gate()

    if kind in ("payment_issue", "refund"):
        reason = "support_escalation:payment" if kind == "payment_issue" else "support_escalation:refund"
        card = copy("handoff.payment_issue", lang, eta="") if kind == "payment_issue" else copy("support.refund", lang, eta="")
        mark_answered(user_id, channel, text)
        await send(bot, user_id, chat_id, card.strip(), None, biz_conn_id, lang)
        handoff(user_id, reason, summary=text[:200])
        return Gate()

    if kind == "angry":
        order = _latest_paid_order(user_id)
        mark_answered(user_id, channel, text)
        if order:
            oid, (amount, _cur), paid_at = order
            when = paid_at.strftime("%m-%d %H:%M") if paid_at else "-"
            card = copy("care.angry_paid", lang, id=oid, amount=amount, paid_at=when, eta="")
        else:
            card = copy("care.angry", lang)
        await send(bot, user_id, chat_id, card.strip(), None, biz_conn_id, lang)
        handoff(user_id, "angry_paid_customer", summary=text[:200])
        return Gate()

    if kind == "human_request":
        mark_answered(user_id, channel, text)
        handoff(user_id, "explicit_human", summary=text[:200])
        await send(bot, user_id, chat_id, t("intent_human", lang), None, biz_conn_id, lang)
        return Gate()

    if kind == "is_bot":
        card, _ = answers.is_bot(lang)
        await send(bot, user_id, chat_id, card, None, biz_conn_id, lang)
        mark_answered(user_id, channel, text, line=hint, card="is_bot")
        first_value("is_bot")
        return Gate()

    if kind == "sensitive":
        card = copy("card.sensitive", lang, line_name=_line_name(intent.line, lang))
        await send(bot, user_id, chat_id, card, None, biz_conn_id, lang)
        precheck.begin(user_id, "prospect", "", channel=channel, line=intent.line)
        precheck.set_flag(user_id, "first", text)
        _emit("answer_card_shown", user_id, card="sensitive")
        return Gate()

    if kind == "renew":
        await send(bot, user_id, chat_id, copy("card.renew_software", lang), kb([[btn(copy("btn.open_buy_bot", lang), style="primary",
                   url=answers._official().get("purchase_bot_url") or answers.BUY_BOT_URL)]]), biz_conn_id, lang)
        mark_answered(user_id, channel, text, line="software", card="renew_software")
        _emit("answer_card_shown", user_id, card="renew_software")
        first_value("renew_software")
        return Gate()

    if kind == "support":
        from . import aftercare
        known = aftercare.match_known(text) if channel == "bot" else None          # 已知问题自助卡(仅 Bot 私聊:Business 不支持回调按钮)
        if known:
            msg = await aftercare.send_known_issue(bot, user_id, chat_id, lang, known, text)
            if msg:
                mark_answered(user_id, channel, text, line=known.get("line"), card="known_issue")
                _emit("answer_card_shown", user_id, card="known_issue")
                first_value("known_issue")
                return Gate()
        precheck.begin(user_id, "support", text, channel=channel)
        return await advance(bot, user_id, chat_id, lang, biz_conn_id)

    if kind == "partner":
        precheck.begin(user_id, "partner", text, channel=channel)
        return await advance(bot, user_id, chat_id, lang, biz_conn_id)

    if kind == "pick":
        card, markup = answers.software_pick_question(lang)
        await send(bot, user_id, chat_id, card, markup, biz_conn_id, lang)
        mark_answered(user_id, channel, text, line="software", card="pick")
        return Gate()

    if kind in ("price_ask", "buy_now"):
        line = intent.line or ("software" if (case_source(user_id) or "").startswith(("web_tg", "app", "src_web_tg", "src_app")) else None)
        if kind == "price_ask" and line == "software" and extract_rules.questions(text) >= 3:
            first_value("ai")                                 # 一句多问:不发价目卡,直接 AI 逐条回答(S-11)
            return await software_to_ai(bot, user_id, chat_id, text, lang, biz_conn_id, channel)
        if kind == "buy_now" or line == "software":
            card, markup = answers.official_buy(lang) if kind == "buy_now" else answers.price_software(lang)
            await send(bot, user_id, chat_id, card, markup, biz_conn_id, lang)
            mark_answered(user_id, channel, text, line="software", card="price_software" if kind == "price_ask" else "official_buy")
            _emit("answer_card_shown", user_id, card="price_software" if kind == "price_ask" else "official_buy")
            first_value("price_software" if kind == "price_ask" else "official_buy")
            return Gate()
        if line in entry.LINE_ANSWER_FIRST:
            first_value(f"{line}_intro")
            return await answer_line(bot, user_id, chat_id, line, text, lang, biz_conn_id, channel)
        card, markup = answers.price_overview(lang)
        await send(bot, user_id, chat_id, card, markup, biz_conn_id, lang)
        mark_answered(user_id, channel, text, card="price_overview")
        first_value("price_overview")
        return Gate()

    if kind == "line_question":
        line = intent.line
        if line == "software":
            first_value("ai")
            return await software_to_ai(bot, user_id, chat_id, text, lang, biz_conn_id, channel)
        if line in entry.LINE_ANSWER_FIRST or line == "other_biz":
            first_value(f"{line}_intro")
            return await answer_line(bot, user_id, chat_id, line, text, lang, biz_conn_id, channel)
        precheck.begin(user_id, "prospect", text, channel=channel)
        return await advance(bot, user_id, chat_id, lang, biz_conn_id)

    if kind == "line_mention":
        line = intent.line
        if line == "software":
            card, markup = answers.price_software(lang)
            await send(bot, user_id, chat_id, card, markup, biz_conn_id, lang)
            mark_answered(user_id, channel, text, line="software", card="price_software")
            first_value("price_software")
            return Gate()
        return await answer_line(bot, user_id, chat_id, line, text, lang, biz_conn_id, channel)

    if kind == "browse":
        card, markup = answers.price_overview(lang)
        await send(bot, user_id, chat_id, card, markup, biz_conn_id, lang)
        mark_answered(user_id, channel, text, card="price_overview")
        first_value("price_overview")
        return Gate()

    # 先答之后的追问:上一张是软件卡 → 软件问题直接交 AI;是定制/IP/AI 卡 → 进入该线问答
    if kind == "unknown" and hint and len(text.strip()) >= 4 and not biz_conn_id_is_greeting(text):
        if hint == "software":
            return await software_to_ai(bot, user_id, chat_id, text, lang, biz_conn_id, channel)
        if hint in entry.LINE_ANSWER_FIRST:
            precheck.begin(user_id, "prospect", text, channel=channel, line=hint)
            return await advance(bot, user_id, chat_id, lang, biz_conn_id)

    # greeting / unknown
    if kind == "unknown" or returning:
        set_intent(user_id, text, channel=channel, role="uncertain")        # 暂存原话,等客户点选
    await _send_welcome(bot, user_id, chat_id, lang, biz_conn_id, channel, returning=returning)
    return Gate()


def biz_conn_id_is_greeting(text: str) -> bool:
    return entry._is_greeting(text, (text or "").lower())


async def notify_pending(bot, user_id: int, chat_id: int, biz_conn_id: str | None = None) -> bool:
    """客户在"待跟进"期间又发消息:回一张"处理中"卡(同一 case 每 30 分钟最多一次)。"""
    lang = lang_for(user_id)
    card = resume.take_status_card(user_id, lang)
    if not card:
        return False
    await send(bot, user_id, chat_id, card, kb([[btn(copy("btn.different_question", lang), callback_data="fd:newcase")]]), biz_conn_id, lang)
    return True


async def note_image_in_intake(bot, user_id: int, chat_id: int, biz_conn_id: str | None = None) -> bool:
    """问答中收到图片:不推进,提示一次(每 case 一次)。"""
    if not in_precheck(user_id):
        return False
    if not engine.image_note(user_id):
        return False
    lang = lang_for(user_id)
    await send(bot, user_id, chat_id, copy("intake.image_received", lang), None, biz_conn_id, lang)
    return True


# ---------- 回调(fd:*) ----------
async def handle_fd_callback(bot, query, data: str, *, edit, send_new, lang: str, biz_conn_id: str | None):
    from ..admin import sandbox
    from ..config import draft_scope
    with draft_scope(sandbox.scope_user(query.from_user.id)):
        return await _handle_fd_callback(bot, query, data, edit=edit, send_new=send_new, lang=lang, biz_conn_id=biz_conn_id)


async def _handle_fd_callback(bot, query, data: str, *, edit, send_new, lang: str, biz_conn_id: str | None):
    """返回 (Gate, alert_text)。edit(text, markup) 原位替换当前卡;send_new(text, markup) 另发新卡。"""
    user_id = query.from_user.id
    chat_id = query.message.chat_id if query.message else user_id
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""
    readonly = (action, arg) in {("go", "software"), ("menu", "more"), ("go", "software_pick"), ("cta", "official_buy")} or action in ("pick", "newcase")
    if is_human_locked(user_id) and not readonly:
        return Gate(), t("pending_short", lang)
    channel = "business" if biz_conn_id else "bot"

    if action == "go" and arg == "software":
        card, markup = answers.price_software(lang)
        await edit(card, markup)
        mark_answered(user_id, channel, line="software", card="price_software")
        _emit("answer_card_shown", user_id, card="price_software")
        _emit("first_value", user_id, card="price_software")
    elif action == "go" and arg == "software_pick":
        card, markup = answers.software_pick_question(lang)
        await edit(card, markup)
    elif action == "pick" and arg in answers.HORIZONS:
        card, markup = answers.software_recommend(arg, 1, lang)
        await edit(card, markup)
    elif action == "go" and arg == "software_ai":
        mark_answered(user_id, channel, line="software")
        await send_new(t("intent_default", lang), None)
    elif action == "go" and arg in entry.LINE_ANSWER_FIRST:
        _emit("first_value", user_id, card=f"{arg}_intro")
        return await answer_line(bot, user_id, chat_id, arg, "", lang, biz_conn_id, channel, edit=edit), None
    elif action == "go" and arg == "other_biz":
        precheck.begin(user_id, "prospect", "", channel=channel, line="other_biz")
        return await advance(bot, user_id, chat_id, lang, biz_conn_id, edit=edit), None
    elif action == "menu" and arg == "more":
        await edit(t("intent_default", lang), more_markup(lang))
    elif action == "role" and arg in ("support", "partner"):
        precheck.begin(user_id, arg, "", channel=channel)
        return await advance(bot, user_id, chat_id, lang, biz_conn_id, edit=edit), None
    elif action in ("ans", "skip", "ok", "fix", "fixslot"):
        try:
            owner = int(parts[2])
        except (ValueError, IndexError):
            return Gate(), "这张卡片已失效,请发送 /start 重新开始"
        if owner != user_id:
            return Gate(), "这不是您的操作按钮喔"
        if action == "ans":
            if not precheck.answer(user_id, parts[3], parts[4]):
                return Gate(), None
        elif action == "skip":
            if not precheck.answer(user_id, parts[3], precheck.SKIP):
                return Gate(), None
        elif action == "ok":
            collapsed = engine.confirm_ok(user_id, lang)
            if collapsed is None:
                return Gate(), None
            await edit(collapsed, None)
            ui = ui_registry.by_message(chat_id, getattr(query.message, "message_id", 0) or 0)
            if ui:
                ui_registry.set_state(ui["id"], "collapsed")
            return await advance(bot, user_id, chat_id, lang, biz_conn_id), None
        elif action == "fix":
            card = engine.fix_list(user_id, lang)
            if card:
                await edit(*card)
            return Gate(), None
        elif action == "fixslot":
            if not engine.fix_slot(user_id, parts[3]):
                return Gate(), None
        return await advance(bot, user_id, chat_id, lang, biz_conn_id, edit=edit), None
    elif action == "resume" and arg == "continue":
        resume.mark_resumed(user_id)
        brief_mod.update(user_id, returning=True)
        _emit("returned", user_id, action="continue")
        if in_precheck(user_id):
            engine._mutate(user_id, 0, lambda st, c: st.__setitem__("resume_prefix", True))
            return await advance(bot, user_id, chat_id, lang, biz_conn_id, edit=edit), None
        await edit(copy("resume.continue_ack", lang), None)
    elif action == "resume" and arg == "new":
        _emit("returned", user_id, action="new")
        set_intent(user_id, "", channel=channel, role="uncertain")
        await edit(copy("welcome", lang), welcome_markup(case_source(user_id), lang))
    elif action == "newcase":
        with SessionMaker() as db:
            db.add(FrontdeskCase(user_id=user_id, business_owner_id=0, channel=channel, state="FRONTDESK"))
            db.commit()
        await edit(copy("welcome", lang), welcome_markup(case_source(user_id), lang))
    elif action == "cta" and arg == "official_buy":
        card, markup = answers.official_buy(lang)
        await send_new(card, markup)
        _emit("cta_clicked", user_id, kind="official_buy")
    return Gate(), None
