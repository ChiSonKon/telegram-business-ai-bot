"""统一 AI 回合网关:Bot 私聊与 Business 共用。

顺序:状态/人工锁 → 熔断 → 额度预占 → 模型调用 → 版本复核 → 出站护栏 → 逐气泡发送 → 结算。
任何一步发现人工已接管(版本号变化)即静默放弃,绝不让过期的 AI 回复送达客户。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from whitecat_core.guardrail import _MONEY, check_reply
from whitecat_core.runtime import epoch_key, epochs, lang_for, llm_breaker

from .frontdesk import abuse, budget, interests, lines
from .frontdesk.persona import assemble_system
from .frontdesk.intake import brief as brief_mod
from .ai import cta, playbook
from .frontdesk.service import (
    POLICY, ROLE_LIMITS, get_role, handoff, in_precheck, is_human_locked, record_message,
    remaining_replies, reserve_reply, settle_reply,
)
from .frontdesk.texts import t
from .humanlike_sender import send_humanlike_messages
from .llm.prompt_loader import load_system_prompt
from .middleware.state_machine import ChatMode, is_ai_mode, set_chat_mode
from .rag.retriever import search_knowledge
from .timeout_tracker import track_activity
from .ui import send_card

logger = logging.getLogger(__name__)

TURN_TIMEOUT = POLICY["quotas"].get("turn_timeout_seconds", 40)
_NUM = re.compile(r"\d+(?:\.\d+)?")
_ADDR = re.compile(r"\b(?:EQ|UQ|kQ|0Q)[A-Za-z0-9_\-]{46}\b|\b0x[a-fA-F0-9]{40}\b|\bT[1-9A-HJ-NP-Za-km-z]{33}\b")
_URL = re.compile(r"(?:https?://|ton://|tg://)[^\s<>\"']+")
_SOFT_WARN = {
    "zh": "\n\n我可以继续帮您确认方案；如果需要深入评估，也可以交给同事接着聊。",
    "en": "\n\nI'm happy to keep refining this with you — or I can pass you to a teammate for a deeper review.",
}


def _clean(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"```[\s\S]*?```", lambda m: m.group().replace("```", ""), text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    return text[:4000]


def _norm(n: str) -> str:
    return n.rstrip("0").rstrip(".") if "." in n else n


def _authorizations(rag: str) -> dict:
    domains = POLICY.get("knowledge", {}).get("allowed_domains", [])
    urls = {f"{p}://{d}" for d in domains for p in ("http", "https")} | set(_URL.findall(rag))
    return {
        "allowed_urls": urls,
        "allowed_addresses": set(_ADDR.findall(rag)),
        "allowed_amounts": {_norm(n) for n in _MONEY.findall(rag)},      # 只授权知识库里带币种单位的金额,而不是任意数字
    }


async def _maybe_offer_leave(bot, user_id, chat_id, biz, lang, role):
    """和 AI 聊过两轮之后,才首次出现'留言给同事'入口(整个会话只提示一次)。"""
    from sqlalchemy import func
    from db.database import SessionMaker
    from db.model import AiUsageLedger
    from .frontdesk.service import take_flag
    from .ui import btn, kb
    with SessionMaker() as db:
        n = db.query(func.count(AiUsageLedger.id)).filter(
            AiUsageLedger.user_id == user_id, AiUsageLedger.role == role, AiUsageLedger.event == "success").scalar() or 0
    from .config import cfg
    if n >= int(cfg.get("escalation.explicit_human.min_ai_turns", default=2) or 2) and take_flag(user_id, "leave_offered"):
        await send_card(bot, chat_id, t("offer_leave", lang),
                        kb([[btn(t("btn_leave", lang), icon="chat", callback_data=f"frontdesk_leave:{user_id}")]]), biz)


def _disclosed(user_id: int) -> bool:
    from .frontdesk import profile
    return profile.disclosed(user_id)


def _emit(event, user_id, **props):
    try:
        from .analytics.events import emit
        emit(event, user_id, **props)
    except Exception:
        pass


async def _fallback(bot, user_id, chat_id, biz, lang, reason, key):
    """降级路径:先落库转人工,再告知客户'留言已保存'。"""
    handoff(user_id, reason)                     # 落库 + 入队管理群通知(见 frontdesk.service.handoff)
    _emit("ai_turn", user_id, ok=False, reason=key)
    set_chat_mode(user_id, ChatMode.HUMAN_MODE)
    await send_card(bot, chat_id, t(key, lang), None, biz)



def _alert_llm_failure(exc: Exception) -> None:
    """AI 调用失败时提醒管理群(同一半小时只提醒一次),带脱敏后的原因,不用再去翻日志。"""
    try:
        import html as _html
        import time as _time
        from .frontdesk.aftercare import mask_keys
        from .outbox import enqueue_admin
        reason = mask_keys(" ".join(str(exc).split()))[:300]
        text = ("⚠️ <b>AI 调用失败</b>(客户已收到离线兜底回复)\n" + _html.escape(reason) + "\n"
                "常见原因:密钥无效/过期(401)、模型名不存在、额度用尽、网络超时。请到「设置 → AI 引擎」检查。")
        enqueue_admin(f"llm_fail:{int(_time.time() // 1800)}", text)
    except Exception:
        logger.warning("llm failure alert not queued")


async def run_ai_turn(*, bot, user_id: int, chat_id: int, texts: list[str], llm, short_memory, long_memory,
                      ticket=None, biz_conn_id: str | None = None, wants_voice: bool = False,
                      mirror=None) -> bool:
    """返回 True 表示成功送达了 AI 回复。mirror(clean_reply, sent_msgs) 用于同步管理群。"""
    ticket = ticket or epochs.ticket(epoch_key(user_id))
    query = "\n".join(texts)
    lang = lang_for(user_id)

    if not ticket.valid or not is_ai_mode(user_id) or is_human_locked(user_id) or in_precheck(user_id):
        return False     # 预检未完成时绝不调用 AI

    role = get_role(user_id)
    if role not in ROLE_LIMITS or ROLE_LIMITS[role] == 0:
        await _fallback(bot, user_id, chat_id, biz_conn_id, lang, "visitor_message" if role == "visitor" else "no_role", "ended")
        return False

    if not llm_breaker.allow():
        await _fallback(bot, user_id, chat_id, biz_conn_id, lang, "system_unavailable", "offline")
        return False

    limit_hit = budget.exhausted()
    if limit_hit:                                   # 全局预算耗尽:不调用模型,留言转人工
        logger.warning("global LLM limit reached: %s", limit_hit)
        await _fallback(bot, user_id, chat_id, biz_conn_id, lang, "budget_exhausted", "offline")
        return False

    rkey = reserve_reply(user_id, role)
    if rkey is None:
        if is_human_locked(user_id):
            return False
        abuse.strike(user_id, "quota")
        await _fallback(bot, user_id, chat_id, biz_conn_id, lang, "quota_exhausted", "quota_ended")
        return False

    attempts, tin, tout = 0, 0, 0
    t0 = time.monotonic()
    draft = None
    try:
        from .experience import drafts
        with_case = None
        try:
            from db.database import SessionMaker
            from db.model import FrontdeskCase
            with SessionMaker() as _db:
                _c = _db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=0).order_by(FrontdeskCase.id.desc()).first()
                with_case = _c.id if _c else None
        except Exception:
            pass
        draft = await drafts.begin(bot, chat_id, user_id, with_case, ROLE_LIMITS[role] - remaining_replies(user_id, role), biz_conn_id)
    except Exception:
        logger.debug("draft start skipped", exc_info=True)
    try:
        tr = interests.track(user_id, query)                                    # 主线 + 并行意向(客户中途提到别的业务时丝滑衔接)
        line = tr.primary
        rag = search_knowledge(query, line_id=line["id"] if line else None,
                               extra_line_ids=[x["id"] for x in tr.others] + ([tr.focus["id"]] if tr.focus else []))
        short_memory.add(user_id, "user", query)
        messages = short_memory.get_history(user_id)
        line_id = line["id"] if line else None
        marker = query.startswith("(") and query.endswith(")")                           # 首轮按钮标记不是客户原话
        brief = playbook.update_brief_from_text(user_id, "" if marker else query, line_id)   # 本条消息里的事实先入简报,阶段按最新事实判定
        turn_no = max(1, ROLE_LIMITS[role] - remaining_replies(user_id, role))        # 含本次预占 = 本轮是第几次 AI 回复
        pb = playbook.load(line_id, role)
        stage = playbook.current_stage(pb, brief, turn_no, lang)
        first_biz = bool(biz_conn_id) and not _disclosed(user_id)
        rules = playbook.turn_instructions(brief, texts, "business" if biz_conn_id else "bot", lang, pb=pb, stage=stage, first_business_reply=first_biz)
        rules = list(rules) + interests.turn_rules(tr, brief, lang)
        has_brief = bool(brief.line or brief.slots or brief.first_message or brief.interests)
        parallel = "\n".join(x for x in (interests.line_briefs(tr.others + ([tr.focus] if tr.focus and tr.focus not in tr.others and tr.focus != tr.primary else []), lang),
                                         interests.playbook_musts(tr.others, lang)) if x)
        system = assemble_system(
            role, lang, line_brief=((lines.brief(line, lang) + ("\n\n" + parallel if parallel else "")) if role == "prospect" else ""),
            playbook_text=playbook.render(pb, stage, lang),
            brief_text=brief_mod.render_for_ai(brief, lang) if has_brief else "", turn_rules=rules, operator_prompt=load_system_prompt())
        tin = (len(system) + len(rag) + sum(len(m["content"]) for m in messages)) // 2
        attempts = 1
        reply = await asyncio.wait_for(
            llm.chat(system_prompt=system, messages=messages, rag_context=rag,
                     long_term_context=long_memory.get_user_summary(user_id, query)),
            TURN_TIMEOUT,
        )
        usage = getattr(llm, "last_usage", None) or {"input_tokens": tin, "output_tokens": len(reply) // 2, "estimated": True}
        tin = usage["input_tokens"] or tin
        llm_breaker.success()
        tout = usage["output_tokens"] or len(reply) // 2
        if draft:
            draft.stop()
    except Exception as exc:
        if draft:
            draft.stop()
        llm_breaker.failure()
        settle_reply(rkey, False, attempts, tin, 0, budget.cost_usd(tin, 0))
        logger.error("AI turn failed for %s: %s", user_id, type(exc).__name__)
        _alert_llm_failure(exc)
        if ticket.valid:
            await _fallback(bot, user_id, chat_id, biz_conn_id, lang, "system_unavailable", "offline")
        return False

    if not ticket.valid:                       # 推理期间被人工接管:丢弃,不发送,不计成功回合
        settle_reply(rkey, False, attempts, tin, tout, budget.cost_usd(tin, tout))
        return False

    reply = _clean(reply)
    verdict = check_reply(reply, **_authorizations(rag))
    if not verdict.ok:
        settle_reply(rkey, False, attempts, tin, tout, budget.cost_usd(tin, tout))
        logger.warning("guardrail blocked reply for %s: %s", user_id, verdict.reasons)
        await _fallback(bot, user_id, chat_id, biz_conn_id, lang, "guardrail:" + ",".join(verdict.reasons), "needs_human")
        return False

    if remaining_replies(user_id, role) == 2:   # 含本次预占:仅剩最后 2 轮时温和提示
        reply += _SOFT_WARN.get(lang, _SOFT_WARN["zh"])

    plain_reply = reply
    if biz_conn_id:
        from .frontdesk.router import take_disclosure
        if take_disclosure(user_id, biz_conn_id):         # Business 里机器人的第一条消息先披露 AI 身份
            from .config import copy
            reply = copy("biz.disclosure", lang) + chr(10) + reply
    sent = await send_humanlike_messages(
        bot=bot, chat_id=chat_id, text=reply, business_connection_id=biz_conn_id,
        force_voice=wants_voice, should_continue=lambda: ticket.valid, lang=lang,
    )
    ok = bool(sent)
    settle_reply(rkey, ok, attempts, tin, tout, budget.cost_usd(tin, tout), bool(usage.get("estimated")))
    if not ok:
        return False

    _emit("ai_turn", user_id, ok=True, latency_ms=int((time.monotonic() - t0) * 1000),
          turn_no=ROLE_LIMITS[role] - remaining_replies(user_id, role))
    short_memory.add(user_id, "assistant", plain_reply)
    long_memory.store(user_id, f"User: {query[:200]}\nAI: {plain_reply[:200]}")
    record_message(user_id, "out", reply, channel="business" if biz_conn_id else "bot")
    track_activity(user_id, chat_id, biz_conn_id, is_ai_reply=True)
    try:                                                        # 把本轮客户文本里的事实写回简报,推进剧本阶段;再执行出口动作 / CTA
        brief = playbook.update_brief_from_text(user_id, "", line_id, clear_pending=True)
        done = await playbook.after_turn(bot, user_id, chat_id, brief, stage, lang, biz_conn_id)
        if not done:
            await cta.maybe_send(bot, user_id, chat_id, line_id, turn_no, lang, biz_conn_id)
    except Exception:
        logger.exception("post-turn playbook step failed for %s", user_id)
    from .config import cfg
    if int(cfg.get("escalation.explicit_human.min_ai_turns", default=0) or 0) > 0:      # 业主否决 D1 时的旧行为:聊满 N 轮才出现"留言给同事"
        await _maybe_offer_leave(bot, user_id, chat_id, biz_conn_id, lang, role)
    if mirror:
        try:
            await mirror(reply, sent)
        except Exception as exc:
            logger.warning("mirror failed: %s", type(exc).__name__)
    return True


# ---------- 共享实例与便捷入口 ----------
_shared = None
_bg_tasks: set = set()


def shared():
    """(llm, short_memory, long_memory) 单例:各入口共享同一份对话记忆,渠道切换不丢上下文。"""
    global _shared
    if _shared is None:
        from .llm.fallback_client import FallbackLLMClient
        from .memory.long_term import LongTermMemory
        from .memory.conversation_store import ConversationStore
        _shared = (FallbackLLMClient(), ConversationStore(max_messages=15), LongTermMemory())
    return _shared


def admin_mirror(bot, user_id: int):
    """把 AI 回复同步到该客户在管理群的话题。"""
    async def _mirror(reply, _sent):
        from db.database import SessionMaker
        from db.model import User
        from .global_settings import get_admin_group_id
        with SessionMaker() as db:
            u = db.query(User).filter(User.user_id == user_id).first()
            thread = u.message_thread_id if u else None
        if thread:
            await bot.send_message(get_admin_group_id(), f"🤖 AI 回复:\n\n{reply}", message_thread_id=thread)
    return _mirror


async def run_first_turn(bot, user_id: int, chat_id: int, texts: list[str], biz_conn_id: str | None = None) -> bool:
    """预检刚完成:立即以预检摘要发起首轮 AI(不经防抖)。"""
    llm, sm, lm = shared()

    def _outs() -> int:
        from db.database import SessionMaker
        from db.model import FrontdeskMessage
        with SessionMaker() as db:
            return db.query(FrontdeskMessage).filter(FrontdeskMessage.user_id == user_id, FrontdeskMessage.direction == "out").count()

    async def _go():
        before = _outs()
        ok = False
        try:
            ok = await run_ai_turn(bot=bot, user_id=user_id, chat_id=chat_id, texts=texts, llm=llm, short_memory=sm, long_memory=lm,
                                   biz_conn_id=biz_conn_id, mirror=admin_mirror(bot, user_id))
        except Exception:
            logger.exception("first AI turn crashed for %s", user_id)
        try:                                   # 兜底:首轮 AI 没有任何回复且客户仍停在 AI 模式 → 告知"已转同事、会在这里回复",并通知管理群,避免客户只看到"需求已记下"
            if not ok and is_ai_mode(user_id) and not is_human_locked(user_id) and _outs() == before:
                logger.warning("first AI turn silent for %s; handing off", user_id)
                await _fallback(bot, user_id, chat_id, biz_conn_id, lang_for(user_id), "ai_silent", "ended")
        except Exception:
            logger.exception("silent-turn fallback failed for %s", user_id)

    task = asyncio.create_task(_go())      # 后台执行:不能阻塞 Telegram 更新队列(否则其他客户的按钮会"没反应")
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return True
