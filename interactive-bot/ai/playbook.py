"""AI 引导剧本(03 §7):阶段由代码判定,渲染进 system;出口动作在 AI 回复之后由代码执行。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ..config import cfg
from ..frontdesk.intake import brief as brief_mod, engine, extract_rules as er

logger = logging.getLogger(__name__)


@dataclass
class Stage:
    id: str
    instruction: str = ""
    action: str | None = None
    say: str = ""
    exit_when: list = field(default_factory=list)


def _t(d, lang: str) -> str:
    return (d or {}).get(lang) or (d or {}).get("zh", "") if isinstance(d, dict) else (d or "")


def load(line: str | None, role: str | None = None) -> dict | None:
    pbs = cfg.get("playbooks", default={}) or {}
    if role == "support":
        return pbs.get("support")
    return pbs.get(line or "")


def current_stage(pb: dict | None, brief: brief_mod.HandoffBrief, turn_no: int, lang: str = "zh") -> Stage | None:
    """第一个"出口条件未满足"的阶段;全部满足或超过 max_turns_before_close → 带 action 的收尾阶段。"""
    if not pb:
        return None
    stages = pb.get("stages", [])
    closing = next((s for s in stages if s.get("action")), None)
    if closing and turn_no >= int(pb.get("max_turns_before_close", 99)):
        return Stage(closing["id"], "", closing["action"], _t(closing.get("say"), lang))
    for s in stages:
        if s.get("action"):
            continue
        satisfied = all(k in brief.slots for k in s.get("exit_when", [])) and turn_no > int(s.get("min_turns", 0))
        if not satisfied:
            return Stage(s["id"], _t(s.get("instruction"), lang), None, "", list(s.get("exit_when", [])))
    if closing:
        return Stage(closing["id"], "", closing["action"], _t(closing.get("say"), lang))
    last = stages[-1] if stages else None
    return Stage(last["id"], _t(last.get("instruction"), lang)) if last else None


def render(pb: dict | None, stage: Stage | None, lang: str) -> str:
    """注入 system 第 [3] 段。"""
    if not pb:
        return ""
    zh = lang != "en"
    out = [("【引导剧本】目标:" if zh else "[Playbook] Goal: ") + _t(pb.get("goal"), lang)]
    if stage:
        out.append(("当前阶段:" if zh else "Current stage: ") + stage.id + ((" — " + stage.instruction) if stage.instruction else ""))
        if stage.action and stage.say:
            out.append(("本轮请用这句话收尾:" if zh else "End this reply with: ") + stage.say)
    mn = (pb.get("must_not") or {}).get(lang) or (pb.get("must_not") or {}).get("zh") or []
    if mn:
        out.append(("禁止:" if zh else "Never: ") + ("; ".join(mn)))
    obj = pb.get("objections") or {}
    if obj:
        out.append(("异议处理(客户提到时参考):" if zh else "Objection handling (when it comes up):"))
        for k, v in obj.items():
            out.append(f"- {k}: {_t(v, lang)}")
    return "\n".join(out)


_PRICE_HIGH = re.compile(r"太贵|贵了|有点贵|便宜点|优惠|expensive|too much|cheaper|discount", re.I)
_COMPETITOR = re.compile(r"别家|其他家|别人|同行|竞品|对比|比价|competitor|another vendor|other vendors", re.I)


def turn_instructions(brief: brief_mod.HandoffBrief, texts: list[str], channel: str, lang: str, *, pb: dict | None = None,
                      stage: Stage | None = None, first_business_reply: bool = False) -> list[str]:
    """03 §7.1 第 [5] 段:本轮指令(规则生成)。"""
    zh = lang != "en"
    text = "\n".join(texts)
    out = []
    n = er.questions(text)
    if n >= 2:
        out.append(f"客户本条消息包含 {n} 个问题,请逐条简短回答(每条一行,最多 5 条),最后只问一个问题。" if zh else
                   f"The customer's message contains {n} questions — answer each briefly, one line each (max 5), then ask a single question.")
    if brief.budget.get("declined"):
        out.append("客户已表示暂不透露预算,不要再询问预算。" if zh else "The customer chose not to share their budget — do not ask about it again.")
    if brief.pending_questions:
        out.append(("先简短回答这些客户尚未得到回答的问题:" if zh else "First briefly answer these still-unanswered questions: ") + "; ".join(brief.pending_questions))
    if channel == "business" and first_business_reply:
        out.append("这是该客户在 Business 会话中的第一条 AI 消息,开头已由系统加了披露行,不要重复自我介绍。" if zh else
                   "This is the first AI message in this Business chat; the system already added the disclosure line — don't introduce yourself again.")
    if brief.returning:
        out.append("这是回访客户,请自然衔接上次的话题(例如\"接着上次说的…\")。" if zh else "Returning customer — pick up naturally from last time (e.g. \"Picking up from before…\").")
    obj = (pb or {}).get("objections") or {}
    if obj.get("price_high") and _PRICE_HIGH.search(text):
        out.append(("客户觉得价格高,参考:" if zh else "The customer finds it pricey — guidance: ") + _t(obj["price_high"], lang))
    if obj.get("competitor") and _COMPETITOR.search(text):
        out.append(("客户在对比同行,参考:" if zh else "The customer is comparing vendors — guidance: ") + _t(obj["competitor"], lang))
    if obj.get("trust") and (brief.signals.get("trust_concern") or er.trust_concern(text)):
        out.append(("客户有信任疑虑,参考:" if zh else "The customer has trust concerns — guidance: ") + _t(obj["trust"], lang))
    return out


# ---------- 把本轮客户文本里的事实写回简报,让阶段推进 ----------
_CHANNEL = re.compile(r"(telegram|tg|群|频道|社群|私域|推特|twitter|小红书|抖音|微信|广告|官网|discord|reddit|youtube|b站|公众号|线下)", re.I)
_SCALE = re.compile(r"((?:日均|每天|每日|一天|每月|月均|每周)\D{0,6}\d+\s*\S{0,3})|(\d+\s*(?:条|个|人|单|次|位)?\s*[/每]?\s*(?:天|日|月|周))")


def extract_playbook_slots(text: str) -> dict:
    out = {}
    m = _CHANNEL.search(text or "")
    if m:
        out["customer_channel"] = m.group(1)
    m = _SCALE.search(text or "")
    if m:
        out["scale"] = (m.group(1) or m.group(2)).strip()
    return out


def update_brief_from_text(user_id: int, text: str, line: str | None, owner_id: int = 0, *, clear_pending: bool = False) -> brief_mod.HandoffBrief:
    """把本条客户文本里的事实写入简报(只补空,不覆盖已有值)。clear_pending=True 表示本轮已回答完待答问题。"""
    ex = er.extract_all(text)
    b = brief_mod.load(user_id, owner_id)
    new = {}
    if line == "software":
        for k in ("os", "horizon"):
            if k in ex:
                new[k] = ex[k]
        if "devices" in ex:
            new["devices"] = engine._ex_devices(text, ex, {}, {})
    new.update(extract_playbook_slots(text))
    for k, v in new.items():
        b.slots.setdefault(k, v)
    for k in ("urgency", "sentiment", "trust_concern", "company", "reseller", "questions"):
        if k in ex:
            b.signals[k] = ex[k]
    if ex.get("money") and not b.budget.get("declined") and not b.budget.get("band"):
        m = ex["money"]
        b.budget["stated"] = f"{m['amount']:g} {m['currency']}"
    if clear_pending:
        b.pending_questions = []                               # 本轮已先回答
    brief_mod.save(user_id, b, owner_id)
    return b


async def after_turn(bot, user_id: int, chat_id: int, brief: brief_mod.HandoffBrief, stage: Stage | None, lang: str,
                     biz_conn_id: str | None = None) -> str | None:
    """AI 回复发出之后执行出口动作(handoff:*)。返回执行的动作名。"""
    if not stage or not stage.action:
        return None
    kind, _, arg = stage.action.partition(":")
    if kind != "handoff":
        return None                                           # cta:* 由 ai.cta 按规则处理
    from ..frontdesk import escalation, router
    from ..frontdesk.service import handoff
    prio = {"quote_request": "P1" if (brief.line == "bot_custom") else "P2", "ai_stuck": "P2"}.get(arg, "P2")
    summary = (brief_mod.render_for_admin(brief) or brief.first_message)[:300]
    handoff(user_id, arg, summary=summary, prio=prio)
    card_key = {"quote_request": "handoff.quote_request", "ai_stuck": "human.done"}.get(arg)
    if card_key:
        from ..config import copy
        await router.send(bot, user_id, chat_id, copy(card_key, lang, eta=escalation.eta_text(prio, lang)).strip(), None, biz_conn_id, lang)
    return stage.action
