"""WP5:AI 深度引导——简报进 system、剧本阶段、CTA、持久记忆、Business 披露、故障降级。含 G-03/04/05/13/21。"""
import importlib
from pathlib import Path

import pytest

from sim import Sim, SimLLM, assert_no_words

PKG = "interactive-bot"
G13 = "hi 你们软件 mac 能用吗?M2 的。多少钱一个月?可以两台电脑用吗?有没有 API?另外 IP 你们也卖吗"
FIRST = "想做个能自动回复客户的机器人,大概多少钱?"
SNAP = Path(__file__).parent / "golden" / "snapshots" / "G04_system.txt"


def to_ai(s, band="300–800"):
    s.say(FIRST)
    s.say("客户问价格能自动回,还能转给我")
    s.click("1 个月内")
    s.click(band)
    s.click("对,继续")


def pb_mod():
    return importlib.import_module(f"{PKG}.ai.playbook")


def brief_mod():
    return importlib.import_module(f"{PKG}.frontdesk.intake.brief")


# ---------- 黄金对话 ----------
def test_G03_brief_in_system_not_user(env):
    s = Sim(env)
    s.say("你们软件能群发吗?会不会封号?")
    assert "【接待简报" in s.llm.last_system
    assert all("简报" not in m["content"] for m in s.llm.last_messages)
    assert s.llm.last_messages[-1]["content"] == "你们软件能群发吗?会不会封号?"
    assert "包含 2 个问题" in s.llm.last_system
    assert "line" not in s.llm.last_messages[-1]["content"]


def test_G04_ai_first_turn_uses_brief(env):
    s = Sim(env)
    to_ai(s)
    sys = s.llm.last_system
    assert "budget.band=300-800" in sys and "key_feature" in sys and "【接待简报" in sys
    assert "最后只问一个问题" in sys                                  # persona 条款 9
    assert s.llm.last_messages[-1]["content"] == FIRST                 # user 消息是客户真实首句
    assert "引导剧本" in sys and "understand" in sys


def test_G04_system_snapshot(env):
    s = Sim(env)
    to_ai(s)
    text = s.llm.last_system
    if not SNAP.exists():
        SNAP.parent.mkdir(parents=True, exist_ok=True)
        SNAP.write_text(text, encoding="utf-8")
    assert text == SNAP.read_text(encoding="utf-8"), "system 提示词发生变化:请人工审阅后再更新 tests_core/golden/snapshots/G04_system.txt"


def test_G05_declined_budget_instruction_present(env):
    s = Sim(env)
    to_ai(s, band="先不说")
    for _ in range(3):
        s.say("嗯,那有什么建议")
        assert "客户已表示暂不透露预算,不要再询问" in s.llm.last_system


def test_G13_multi_question_instruction(env):
    s = Sim(env)
    s.say(G13)
    assert "客户本条消息包含 5 个问题,请逐条简短回答" in s.llm.last_system
    assert "macos_arm" in s.llm.last_system or "os(" in s.llm.last_system


def test_G21_model_failure_degrades(env):
    llm = SimLLM(exc=RuntimeError("401 unauthorized"))
    s = Sim(env, llm=llm)
    s.say(FIRST)
    s.say("客户问价格能自动回,还能转给我")
    s.click("1 个月内")
    s.click("先不说")
    out = s.click("对,继续")
    assert "暂时没能生成回复" in "\n".join(o["text"] for o in out) and "不需要重复发送" in out[-1]["text"]
    assert s.case.handoff_reason == "system_unavailable" and env.service.is_human_locked(7)
    for _ in range(3):                                                  # 熔断打开
        env.runtime.llm_breaker.failure()
    assert not env.runtime.llm_breaker.allow()
    s2 = Sim(env, uid=8, llm=SimLLM(exc=RuntimeError("down")))
    s2.start("src_web_tg")
    assert "授权价格" in s2.say("多少钱")[0]["text"]                       # G-01 不依赖模型
    s3 = Sim(env, uid=9, llm=SimLLM(exc=RuntimeError("down")))
    out = s3.say(FIRST)
    assert "最重要的一个功能" in out[-1]["text"] and s3.llm_calls == 0     # G-04 问答部分不依赖模型


# ---------- 剧本与 CTA ----------
def test_playbook_stage_progression(env):
    pb = pb_mod().load("bot_custom")
    b = brief_mod().HandoffBrief(line="bot_custom", slots={"need": "x"})
    st = pb_mod().current_stage
    assert st(pb, b, 1).id == "understand"
    b.slots.update(key_feature="y", customer_channel="telegram 群")
    assert st(pb, b, 2).id == "shape"
    b.slots["scale"] = "日均 50"
    s = st(pb, b, 3)
    assert s.id == "close" and s.action == "handoff:quote_request" and "报价" in s.say
    b2 = brief_mod().HandoffBrief(line="bot_custom", slots={"need": "x"})
    assert st(pb, b2, 4).id == "close"                                  # 超过 max_turns_before_close 强制收尾


def test_playbook_progression_in_conversation_and_quote_handoff(env):
    s = Sim(env)
    to_ai(s)
    assert "understand" in s.llm.last_system
    s.say("我们客户主要从 telegram 群里来")
    assert "shape" in s.llm.last_system
    out = s.say("日均大概 50 条咨询")
    assert "close" in s.llm.last_system and "这些信息够出一份报价单了" in s.llm.last_system
    assert env.service.is_human_locked(7) and s.case.handoff_reason == "quote_request" and s.case.priority == "P1"
    assert "已交给同事出报价" in out[-1]["text"]


def test_cta_once_after_turn_two(env):
    s = Sim(env)
    o1 = s.say("你们软件能群发吗?会不会封号?")
    assert not any(b.url for o in o1 for b in s.buttons(o))
    o2 = s.say("支持多开吗,另外怎么付款")
    cards = [o for o in o2 if o["markup"] and any(b.url for b in s.buttons(o))]
    assert len(cards) == 1 and "官方购买入口" in cards[0]["text"]
    o3 = s.say("好的我考虑下")
    assert not any(o["markup"] for o in o3)                             # 只发一次


def test_turn_no_and_events(env):
    s = Sim(env)
    s.say("你们软件能群发吗?会不会封号?")
    with env.db.SessionMaker() as db:
        evs = db.query(env.model.FunnelEvent).filter_by(event="ai_turn").all()
    assert evs


# ---------- 记忆 ----------
def test_memory_survives_restart(env):
    store_mod = importlib.import_module(f"{PKG}.memory.conversation_store")
    s = Sim(env)
    s.say("你们软件能群发吗?会不会封号?")
    env.ai_turn._shared = None                                          # 模拟重启:进程内单例丢失
    llm, mem, lm = env.ai_turn.shared()
    assert isinstance(mem, store_mod.ConversationStore)
    h = mem.get_history(7)
    assert [m["role"] for m in h] == ["user", "assistant"] and "群发" in h[0]["content"]


def test_staff_reply_visible_to_ai(env):
    store_mod = importlib.import_module(f"{PKG}.memory.conversation_store")
    s = Sim(env)
    s.say("你们软件能群发吗?会不会封号?")
    store_mod.ConversationStore().add(7, "staff", "我是小李,这个功能我们可以演示给您看")
    s.say("好的,什么时候可以")
    contents = [m["content"] for m in s.llm.last_messages]
    assert any(c.startswith("[同事] ") and "演示" in c for c in contents)


def test_memory_purge_older_than(env):
    store_mod = importlib.import_module(f"{PKG}.memory.conversation_store")
    st = store_mod.ConversationStore()
    st.add(7, "user", "old")
    env.runtime.shift_time(days=100)
    st.add(7, "user", "new")
    assert st.purge_older_than(90) == 1 and [m["content"] for m in st.recent(7)] == ["new"]


# ---------- Business ----------
def test_business_first_ai_reply_has_disclosure_once(env):
    env.service.set_intent(7, "x", role="prospect")                     # AI 阶段的第一条消息就是 AI 回复
    s = Sim(env, channel="business")
    out = s.say("你们软件能群发吗?会不会封号?")
    assert out[-1]["text"].startswith("(白猫工作室 AI 助理代为接待") and out[-1]["biz"] == "biz-conn-1"
    assert "开头已由系统加了披露行" in s.llm.last_system
    out2 = s.say("支持多开吗")
    assert "AI 助理代为接待" not in out2[0]["text"]
    assert "开头已由系统加了披露行" not in s.llm.last_system
    assert all("AI 助理代为接待" not in m["content"] for m in s.llm.last_messages)    # 披露行不进入记忆


def test_business_ack_carries_disclosure_when_it_is_first_message(env):
    s = Sim(env, channel="business")
    out = s.say("你们软件能群发吗?会不会封号?")
    assert out[0]["text"].startswith("(白猫工作室 AI 助理代为接待") and "AI 助理代为接待" not in out[-1]["text"]


# ---------- persona / 其他 ----------
def test_persona_clauses_and_assemble(env):
    persona = importlib.import_module(f"{PKG}.frontdesk.persona")
    zh = persona.assemble_system("prospect", "zh", brief_text="【接待简报】", turn_rules=["规则A"], operator_prompt="运营说明")
    assert "8. 系统会提供【接待简报】" in zh and "9. 客户在一条消息里问了多个问题时" in zh
    assert zh.index("【接待简报】") < zh.index("【本轮指令】") < zh.index("[运营补充说明")
    en = persona.assemble_system("prospect", "en")
    assert "8. The system provides an [Intake brief]" in en and "9. If the customer asks several questions" in en


def test_support_playbook_hands_off_after_two_turns(env):
    env.service.set_intent(7, "x", role="support")
    s = Sim(env)
    s.say("软件装完打不开,怎么办")
    assert "troubleshoot" in s.llm.last_system and not env.service.is_human_locked(7)
    s.say("还是打不开")
    assert env.service.is_human_locked(7) and s.case.handoff_reason == "ai_stuck"
