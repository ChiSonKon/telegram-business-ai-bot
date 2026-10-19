"""WP2:埋点与漏斗。"""
import importlib
import json
from datetime import datetime, timedelta

import pytest

from sim import Sim

PKG = "interactive-bot"


def mods(env):
    return (importlib.import_module(f"{PKG}.analytics.events"), importlib.import_module(f"{PKG}.analytics.funnel"))


def rows(env, **flt):
    with env.db.SessionMaker() as s:
        q = s.query(env.model.FunnelEvent)
        for k, v in flt.items():
            q = q.filter(getattr(env.model.FunnelEvent, k) == v)
        return q.all()


def window():
    return datetime.utcnow() - timedelta(hours=1), datetime.utcnow() + timedelta(hours=1)


def test_emit_rejects_unknown_event(env):
    events, _ = mods(env)
    with pytest.raises(ValueError):
        events.emit("entyr", 7)


def test_emit_strips_text_props(env):
    events, _ = mods(env)
    events.emit("entry", 7, intent="price_ask", note="客户说想买一个机器人并且预算很多很多很多很多很多很多很多很多很多", raw="x" * 50)
    props = json.loads(rows(env, event="entry")[0].props_json)
    assert props == {"intent": "price_ask"}


def test_emit_never_raises_on_db_error(env, monkeypatch):
    events, _ = mods(env)

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(events, "SessionMaker", boom)
    events.emit("entry", 7, intent="x")                     # 不抛


def test_funnel_counts_from_golden_flow(env):
    _, funnel = mods(env)
    s1, s2 = Sim(env, uid=7), Sim(env, uid=8)
    s1.start("src_web_tg")
    s1.say("多少钱")                                        # G-01
    s2.say("想做个能自动回复客户的机器人,大概多少钱?")          # G-04 前半:先答卡 + 进入问答
    f = funnel.funnel(*window())
    assert f["F1"] == 2 and f["F2"] == 2
    by = {r.event for r in rows(env)}
    assert {"entry", "first_value", "answer_card_shown", "intake_started", "intake_step_shown"} <= by
    sources = funnel.by_source(*window())
    assert any(s["source"] == "web_tg" and s["entries"] == 1 for s in sources)


def test_dropoff_rate(env):
    events, funnel = mods(env)
    for i in range(10):
        events.emit("intake_step_shown", 100 + i, slot="budget", flow="prospect")
    for i in range(6):
        events.emit("intake_answered", 100 + i, slot="budget", flow="prospect", method="button")
    d = funnel.dropoffs(*window())
    assert d[0][:4] == ("prospect", "budget", 10, 6) and abs(d[0][4] - 0.4) < 1e-9


def test_test_events_excluded(env):
    _, funnel = mods(env)
    env.sandbox.set_client_view(7, True)
    Sim(env, uid=7).say("多少钱")
    assert rows(env, event="entry") and rows(env, event="entry")[0].test is True
    assert funnel.funnel(*window())["F1"] == 0


def test_human_first_reply_and_handoff_events(env):
    s = Sim(env)
    s.say("我已经付款了但是一直没到账")
    s.say("昨天下午 3 点转的 288 USDT")                           # WP4:无订单先问一句,下一条转交
    h = rows(env, event="handoff")
    assert h and json.loads(h[0].props_json)["prio"] == "P0"
    env.runtime.shift_time(minutes=7)
    env.service.record_human_reply(7)
    env.service.record_human_reply(7)                          # 同一次转交只记一次
    r = rows(env, event="human_first_reply")
    assert len(r) == 1 and json.loads(r[0].props_json)["wait_min"] >= 7


def test_ai_effective_once_per_turn(env):
    s = Sim(env)
    s.say("你们软件能群发吗?会不会封号?")                       # AI 回合
    s.say("好的我再看看")                                       # 30 分钟内继续回应 → ai_effective
    assert len(rows(env, event="ai_effective")) == 1
    assert len(rows(env, event="ai_turn")) >= 1


def test_intake_abandon_job_once(env):
    jobs = importlib.import_module(f"{PKG}.analytics.jobs")
    s = Sim(env)
    s.say("想做个能自动回复客户的机器人,大概多少钱?")
    assert jobs.intake_abandon_job() == 0
    s.advance(minutes=45)
    assert jobs.intake_abandon_job() == 1
    assert jobs.intake_abandon_job() == 0
    assert len(rows(env, event="intake_abandoned")) == 1


def test_customer_profile_replaces_temp_flags(env):
    profile = importlib.import_module(f"{PKG}.frontdesk.profile")
    with env.db.SessionMaker() as s:
        s.add(env.model.AppSetting(key="biz_disclosed:7", value="1"))
        s.commit()
    assert profile.take_disclosure(7) is False                # 旧临时标记被迁入
    assert profile.take_disclosure(8) is True and profile.take_disclosure(8) is False
    profile.mark_bot_started(8)
    assert profile.bot_started(8) is True


def test_home_digest_and_data_page(env):
    console = importlib.import_module(f"{PKG}.admin.console")
    views = importlib.import_module(f"{PKG}.admin.views")
    s = Sim(env)
    s.say("多少钱")
    text, _ = views.home()
    assert "进线 1" in text and "有用回应 1" in text
    page, markup = views.data_page(7)
    assert "漏斗" in page and "卡点" in page and "来源" in page
    digest = console.digest_text()
    assert "昨日白猫前台" in digest and "待处理" in digest
    s.start("src_web_tg")
    env.runtime.shift_time(days=1)
    s2 = Sim(env, uid=8)
    s2.say("价格")
    assert "进线" in console.digest_text()
