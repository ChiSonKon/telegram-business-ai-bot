"""WP4:人工可达与升级矩阵。含 G-06/07/16/18/19/22。"""
import importlib
from datetime import datetime, timezone

import pytest

from sim import Sim, assert_no_words

PKG = "interactive-bot"


def esc_mod():
    return importlib.import_module(f"{PKG}.frontdesk.escalation")


def make_paid_order(env, uid=7, oid="PAIDORDER1", amount="288.07"):
    with env.db.SessionMaker() as s:
        s.add(env.model.Order(id=oid, user_id=uid, sku="custom", title="定制机器人", state="paid", currency="USDT",
                              amount_str=amount, paid_at=datetime.utcnow()))
        s.commit()


def last_handoff(env, uid=7):
    with env.db.SessionMaker() as s:
        c = s.query(env.model.FrontdeskCase).filter_by(user_id=uid).order_by(env.model.FrontdeskCase.id.desc()).first()
        return c.state, c.handoff_reason, c.priority


# ---------- 黄金对话 ----------
def test_G06_large_deal_direct_handoff(env):
    s = Sim(env)
    out = s.say("我们公司要做一套客服系统,预算 5000U,需要合同")
    state, reason, prio = last_handoff(env)
    assert state == "HUMAN_PENDING" and reason == "large_deal" and prio == "P1"
    assert "优先处理" in out[-1]["text"] and ("分钟内" in out[-1]["text"] or "上午" in out[-1]["text"])
    assert s.llm_calls == 0
    assert any("5000U" in t for t in s.outbox())


def test_G07_explicit_human_one_question(env):
    s = Sim(env)
    s.say("你好")
    out = s.say("我要找真人")
    assert "用一句话说说您要办的事" in out[0]["text"]
    assert_no_words(out, ["先完成", "先和 AI"])
    assert not env.service.is_human_locked(7)
    out = s.say("上次定制的机器人要加功能")
    state, reason, prio = last_handoff(env)
    assert state == "HUMAN_PENDING" and reason == "explicit_human" and prio == "P2"
    assert "已交给同事" in out[0]["text"]
    before = s.llm_calls
    s.say("还有一个问题")                                     # 之后客户再发消息 AI 不回复
    assert s.llm_calls == before == 0


def test_G07_business_channel(env):
    s = Sim(env, channel="business")
    s.say("我要找真人")
    out = s.say("想问下我的订单")
    assert env.service.is_human_locked(7) and out[0]["biz"] == "biz-conn-1"


def test_G16_angry_paid_customer_p0(env):
    make_paid_order(env)
    s = Sim(env)
    out = s.say("人呢?是不是骗子")
    text = out[-1]["text"]
    assert "PAIDORDER1" in text and "288.07 USDT" in text and "已加急" in text
    assert last_handoff(env)[1:] == ("angry_paid_customer", "P0")
    assert s.llm_calls == 0


def test_G18_license_p0_asks_first4(env):
    s = Sim(env)
    out = s.say("在你们机器人买的卡密激活不了")
    assert "前 4 位" in out[-1]["text"] and "不要把完整卡密发给任何人" in out[-1]["text"]
    assert not env.service.is_human_locked(7) and s.llm_calls == 0
    out = s.say("AB12,昨天下午买的")
    assert last_handoff(env)[1:] == ("paid_but_broken", "P0")
    assert "授权的同事" in out[0]["text"] and s.llm_calls == 0


def test_G19_refund_p0(env):
    s = Sim(env)
    out = s.say("能退款吗")
    assert last_handoff(env)[1:] == ("support_escalation:refund", "P0")
    assert "24 小时" not in out[0]["text"] and "退款需要同事核对" in out[0]["text"]
    assert s.llm_calls == 0


def test_G22_start_keeps_pending_and_new_question(env):
    s = Sim(env)
    s.say("能退款吗")
    outs = s.start()
    assert last_handoff(env)[0] == "HUMAN_PENDING"
    assert "已在处理中" in outs[0]["text"] and "退款" in outs[0]["text"]
    assert [b.text for b in s.buttons(outs[0])] == ["这是另一个新问题"]
    out = s.click("这是另一个新问题")
    assert "欢迎来到白猫工作室" in out[-1]["text"]
    with env.db.SessionMaker() as db:
        assert {c.state for c in db.query(env.model.FrontdeskCase).all()} == {"HUMAN_PENDING", "FRONTDESK"}


# ---------- 规则表 ----------
def C(**kw):
    return esc_mod().Ctx(**kw)


RULE_CASES = [
    (dict(intent="payment_issue"), "payment_issue", True),
    (dict(intent="payment_issue", has_open_order=True), "payment_issue", False),
    (dict(intent="refund"), "refund", False),
    (dict(intent="support", text="卡密激活不了"), "paid_but_broken", True),
    (dict(intent="support", text="订单出了点问题怎么办", has_paid_order=True), "paid_but_broken", True),
    (dict(intent="angry", has_paid_order=True), "angry_paid_customer", False),
    (dict(intent="line_question", ex={"money": {"amount": 3000.0, "currency": "U"}}, text="预算 3000U 做个机器人需求很清楚"), "large_deal", False),
    (dict(intent="line_question", line="bot_custom", ex={"company": True}, text="我们公司要做个"), "large_deal", True),
    (dict(intent="line_question", line="network", ex={"qty": 200}), "bulk", False),
    (dict(intent="human_request"), "explicit_human", True),
    (dict(intent="line_question", complaints=2, in_ai_stage=True), "ai_stuck", False),
]


@pytest.mark.parametrize("kw,rule,asks", RULE_CASES)
def test_escalation_rule_order_table(env, kw, rule, asks):
    e = esc_mod().evaluate(C(**kw))
    assert e and e.rule_id == rule and bool(e.ask_first) == asks


@pytest.mark.parametrize("kw", [
    dict(intent="price_ask"), dict(intent="line_question", line="software"), dict(intent="greeting"),
    dict(intent="support", text="windows 提示保护你的电脑,装不上", has_paid_order=True),       # 安装类不算"已付款但出问题"
    dict(intent="angry", has_paid_order=False), dict(intent="line_question", complaints=1, in_ai_stage=True),
    dict(intent="line_question", line="software", ex={"company": True}),
])
def test_escalation_non_hits(env, kw):
    assert esc_mod().evaluate(C(**kw)) is None


def test_explicit_human_raises_priority_with_history_orders(env):
    assert esc_mod().evaluate(C(intent="human_request", has_paid_order=True)).prio == "P1"
    assert esc_mod().evaluate(C(intent="human_request")).prio == "P2"


def test_locked_p0_rules_cannot_be_removed(env, monkeypatch):
    config = importlib.import_module(f"{PKG}.config")
    miss = config._MISSING
    fake = [{"id": "payment_issue", "enabled": False, "prio": "P3"}, {"id": "explicit_human", "enabled": False}]
    monkeypatch.setattr(config, "_override", lambda k: fake if k == "escalation.rules" else miss)
    e = esc_mod()
    assert e.evaluate(C(intent="payment_issue")).prio == "P0"          # 锁定项:禁用/降级无效
    assert e.evaluate(C(intent="refund")).rule_id == "refund"           # 未出现在覆盖表里也仍在
    assert e.evaluate(C(intent="human_request")) is None                # 非锁定项可被禁用
    ids = {r["id"] for r in e.rules() if r.get("locked")}
    assert ids == {"payment_issue", "refund", "paid_but_broken"}


def test_escalation_disabled_falls_back_to_immediate_handoff(env, monkeypatch):
    config = importlib.import_module(f"{PKG}.config")
    miss = config._MISSING
    monkeypatch.setattr(config, "_override", lambda k: False if k == "escalation.enabled" else miss)
    s = Sim(env)
    s.say("我要找真人")
    assert env.service.is_human_locked(7)                               # WP3 之前的行为:不先问一句


# ---------- 等待时间 ----------
@pytest.mark.parametrize("hour_utc,prio,expect", [
    (6, "P0", "通常 10 分钟内"), (6, "P1", "通常 30 分钟内"),
    (18, "P0", "已加急,最晚上午 9 点前"), (18, "P1", "现在是夜间,同事会在上午 9 点前回复您"),
])
def test_eta_daytime_and_night(env, hour_utc, prio, expect):
    now = datetime(2026, 10, 13, hour_utc, 0, tzinfo=timezone.utc)          # 14:00 / 次日 02:00 (UTC+8)
    assert expect in esc_mod().eta_text(prio, "zh", now)
    assert esc_mod().eta_text(prio, "en", now)


# ---------- 愤怒 / AI 受阻 ----------
def test_angry_without_order_not_handed_off(env):
    s = Sim(env)
    out = s.say("你们是骗子吧")
    assert "抱歉让您不愉快" in out[0]["text"] and not env.service.is_human_locked(7)


def test_ai_stuck_after_two_complaints(env):
    env.service.set_intent(7, "x", role="prospect")
    s = Sim(env)
    s.say("这个没用")
    assert s.llm_calls == 1 and not env.service.is_human_locked(7)
    out = s.say("还是不对")
    assert env.service.is_human_locked(7) and last_handoff(env)[1] == "ai_stuck" and s.llm_calls == 1
    assert "已交给同事" in out[0]["text"]


def test_ai_stuck_counter_resets_after_good_turn(env):
    env.service.set_intent(7, "x", role="prospect")
    s = Sim(env)
    s.say("这个没用")
    s.say("那请问有试用吗")
    s.say("不对")
    assert not env.service.is_human_locked(7)


def test_human_request_cancel(env):
    s = Sim(env)
    s.say("我要找真人")
    out = s.say("算了")
    assert "有需要随时说" in out[0]["text"] and not env.service.is_human_locked(7)
    out = s.say("多少钱")
    assert "服务与参考价" in out[0]["text"] or "授权价格" in out[0]["text"]


def test_never_trapped_two_human_requests(env):
    for state_setup in (None, "precheck", "ai"):
        uid = {None: 20, "precheck": 21, "ai": 22}[state_setup]
        s = Sim(env, uid=uid)
        if state_setup == "precheck":
            env.precheck.begin(uid, "prospect", "我想了解机器人定制")
        if state_setup == "ai":
            env.service.set_intent(uid, "x", role="prospect")
        s.say("我要找真人")
        s.say("我要找真人")
        assert env.service.is_human_locked(uid), state_setup


def test_handoff_notification_contains_brief(env):
    s = Sim(env)
    s.say("想做个能自动回复客户的机器人,大概多少钱?")
    s.say("客户问价格能自动回,还能转给我")
    s.say("我要找真人")
    s.say("想直接和同事谈")
    note = [t for t in s.outbox() if "需要跟进" in t][-1]
    assert "接待简报" in note and "核心功能" in note


def test_owner_alert_for_stale_p0(env):
    from datetime import timedelta
    s = Sim(env)
    s.say("能退款吗")
    with env.db.SessionMaker() as db:
        c = db.query(env.model.FrontdeskCase).filter_by(user_id=7).one()
        c.handoff_at = datetime.utcnow() - timedelta(minutes=12)
        db.commit()
    import asyncio
    console = importlib.import_module(f"{PKG}.admin.console")
    n = asyncio.run(console.sla_check(s.bot))
    assert n >= 2
    with env.db.SessionMaker() as db:
        dms = [r for r in db.query(env.model.Outbox).all() if r.chat_id == 1]
    assert dms and "无人接手" in dms[0].text
