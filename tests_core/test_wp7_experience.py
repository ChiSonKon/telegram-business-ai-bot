"""WP7:体验层——特效、置顶、收起、反应、思考中占位;失败自动降级,Business 不用不稳定的能力。"""
import asyncio
import importlib
from types import SimpleNamespace

import pytest

from sim import Sim, SimBot, SimLLM

PKG = "interactive-bot"


def mod(name):
    return importlib.import_module(f"{PKG}.{name}")


def run(coro):
    return asyncio.run(coro)


# ---------- 特效 ----------
def test_effect_only_on_allowed_moments_and_private(env):
    fx = mod("experience.effects")
    assert fx.effect_kwargs(7, "order.paid", "bot") == {"message_effect_id": fx.DEFAULT_PARTY_ID}
    assert fx.effect_kwargs(7, "order.delivered", "bot")
    assert fx.effect_kwargs(7, "order.paid", "business") == {}           # Business 不发特效
    for moment in ("order.underpaid", "order.checking", "intake.done", "ai.reply", "support.refund"):
        assert fx.effect_kwargs(7, moment, "bot") == {}                  # 少付/核对中/问答/AI/售后一律没有


def test_effect_cooldown_24h(env):
    fx = mod("experience.effects")
    bot = SimBot()
    run(fx.send(bot, 7, "已收款", None, user_id=7, moment="order.paid"))
    assert bot.outs[-1]["effect"]
    run(fx.send(bot, 7, "已收款 2", None, user_id=7, moment="order.paid"))
    assert bot.outs[-1]["effect"] is None                                # 24 小时内第二次不再发特效
    env.runtime.shift_time(hours=25)
    run(fx.send(bot, 7, "已收款 3", None, user_id=7, moment="order.paid"))
    assert bot.outs[-1]["effect"]


def test_effect_failure_resends_without_effect_and_disables(env):
    fx, caps = mod("experience.effects"), mod("experience.capabilities")

    class PickyBot(SimBot):
        async def send_message(self, **kw):
            if kw.get("message_effect_id"):
                from telegram.error import BadRequest
                raise BadRequest("Message effect not found")
            return await super().send_message(**kw)
    bot = PickyBot()
    msg = run(fx.send(bot, 7, "已收款", None, user_id=7, moment="order.paid"))
    assert msg is not None and bot.outs[-1]["effect"] is None            # 去掉特效重发,消息照样送达
    assert caps.ok("bot", "effect") is False
    assert fx.effect_kwargs(8, "order.paid", "bot") == {}                # 24 小时内不再尝试
    env.runtime.shift_time(hours=25)
    assert caps.ok("bot", "effect") is True


def test_effects_can_be_switched_off_by_config(env, monkeypatch):
    fx, config = mod("experience.effects"), mod("config")
    miss = config._MISSING
    monkeypatch.setattr(config, "_override", lambda k: False if k == "experience.effects.enabled" else miss)
    assert fx.effect_kwargs(7, "order.paid", "bot") == {}


# ---------- 置顶 ----------
def test_pin_and_scheduled_unpin(env):
    pins, reg = mod("experience.pins"), mod("experience.ui_registry")
    bot = SimBot()
    ui = reg.record(7, 7, 100, "order", ref="ORD1")
    assert run(pins.pin_card(bot, ui)) is True
    assert bot.api_calls("pin_chat_message")[0]["disable_notification"] is True
    assert bot.api_calls("delete_message")[0]["message_id"] == 101       # 尽力删除置顶服务消息
    pins.schedule_unpin(ui, 24 * 3600)
    assert run(pins.unpin_job(bot)) == 0
    env.runtime.shift_time(hours=25)
    assert run(pins.unpin_job(bot)) == 1 and bot.api_calls("unpin_chat_message")
    assert reg.get(ui)["pinned"] is False


def test_pin_never_in_business_by_default(env):
    pins, reg = mod("experience.pins"), mod("experience.ui_registry")
    bot = SimBot()
    ui = reg.record(7, 7, 100, "order", biz="biz-1")
    assert run(pins.pin_card(bot, ui)) is False and not bot.api_calls("pin_chat_message")


def test_pin_failure_disables_capability(env):
    pins, reg, caps = mod("experience.pins"), mod("experience.ui_registry"), mod("experience.capabilities")

    class NoPin(SimBot):
        async def pin_chat_message(self, **kw):
            raise RuntimeError("not enough rights")
    ui = reg.record(7, 7, 100, "order")
    assert run(pins.pin_card(NoPin(), ui)) is False
    assert caps.ok("bot", "pin") is False


# ---------- 收起 / 整洁 ----------
def test_collapse_after_intake_complete(env):
    s = Sim(env)
    s.say("想做个能自动回复客户的机器人,大概多少钱?")
    s.say("客户问价格能自动回,还能转给我")
    s.click("1 个月内")
    s.click("先不说")
    reg = mod("experience.ui_registry")
    live = reg.live(7, "intake")
    assert len(live) <= 1                                                  # 文字作答时旧进度卡已去按钮,只保留最新一张
    s.say("对")                                                            # 文字确认
    assert not reg.live(7, "intake")
    edits = [kw for n, kw in s.bot.calls if n == "edit_message_text"]
    assert any("✓ 需求已记下" in kw["text"] for kw in edits)               # 进度卡收起


def test_old_intake_cards_lose_buttons_when_new_card_sent(env):
    s = Sim(env)
    s.say("想做个能自动回复客户的机器人,大概多少钱?")
    s.say("客户问价格能自动回,还能转给我")
    strips = s.bot.api_calls("edit_message_reply_markup")
    assert strips and strips[0]["reply_markup"] is None


def test_welcome_superseded(env):
    s = Sim(env)
    s.say("嗯")
    s.say("哦哦")
    voids = [kw for n, kw in s.bot.calls if n == "edit_message_text"]
    assert voids and "已更新" in voids[0]["text"]


def test_known_issue_collapse_helpers(env):
    cleanup, reg = mod("experience.cleanup"), mod("experience.ui_registry")
    bot = SimBot()
    ui = reg.record(7, 7, 50, "known_issue")
    assert run(cleanup.collapse(bot, ui, "✓ 已解决")) is True
    assert reg.get(ui)["state"] == "collapsed" and bot.api_calls("edit_message_text")[0]["text"] == "✓ 已解决"
    tmp = reg.record(7, 7, 60, "checking")
    assert run(cleanup.delete_temp(bot, tmp)) is True and reg.get(tmp)["state"] == "deleted"


# ---------- 反应 ----------
def test_reaction_on_receipt_private_only(env):
    rx = mod("experience.reactions")
    bot = SimBot()
    assert run(rx.receipt(bot, 7, 55)) is True
    assert bot.api_calls("set_message_reaction")[0]["message_id"] == 55
    assert run(rx.receipt(bot, 7, 56, channel="business")) is False        # Business 没有反应能力
    assert len(bot.api_calls("set_message_reaction")) == 1
    assert run(rx.pending_ack(bot, 7, 57)) is True


# ---------- 思考中占位 ----------
def test_draft_started_in_bot_not_business(env):
    s = Sim(env)
    s.say("你们软件能群发吗?会不会封号?")
    drafts = [a for m, a in s.bot.calls if m == "sendMessageDraft"]
    assert drafts and drafts[0]["text"] == "" and drafts[0]["can_stop"] is True and drafts[0]["draft_id"] > 0
    b = Sim(env, channel="business", uid=9)
    b.say("你们软件能群发吗?会不会封号?")
    assert not [1 for m, a in b.bot.calls if m == "sendMessageDraft"]


def test_draft_failure_falls_back_and_disables(env):
    caps = mod("experience.capabilities")

    class NoDraft(SimBot):
        async def do_api_request(self, method, api_kwargs=None, **kw):
            if method == "sendMessageDraft":
                raise RuntimeError("method not found")
            return await super().do_api_request(method, api_kwargs, **kw)
    s = Sim(env)
    s.bot = NoDraft()
    out = s.say("你们软件能群发吗?会不会封号?")
    assert any("好的,我来帮您看看" in o["text"] for o in out)            # 回复照常送达
    assert caps.ok("bot", "draft") is False


def test_stop_generation_bumps_epoch_and_sends_nothing(env):
    drafts_mod = mod("experience.drafts")
    gate = {}

    class SlowLLM(SimLLM):
        async def chat(self, **kw):
            self.calls += 1
            gate["event"].set()
            await gate["release"].wait()
            return "不会被发送的回复"

    s = Sim(env, llm=SlowLLM())
    env.service.set_intent(7, "x", role="prospect")

    async def scenario():
        gate["event"], gate["release"] = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(env.ai_turn.run_ai_turn(bot=s.bot, user_id=7, chat_id=7, texts=["你们软件能群发吗"], llm=s.llm,
                                                           short_memory=s.mem, long_memory=s.lm))
        await gate["event"].wait()
        draft_id = next(a["draft_id"] for m, a in s.bot.calls if m == "sendMessageDraft")
        upd = SimpleNamespace(api_kwargs={"stopped_message_generation": {"chat": {"id": 7}, "draft_id": draft_id}})
        await drafts_mod.on_stop_generation(upd, SimpleNamespace())
        gate["release"].set()
        return await task
    ok = run(scenario())
    assert ok is False
    assert not [o for o in s.bot.outs if "不会被发送" in o["text"]]       # 本轮作废:不发送任何内容
    with env.db.SessionMaker() as db:
        assert db.query(env.model.FunnelEvent).filter_by(event="ai_stopped").count() == 1
        assert db.query(env.model.AiUsageLedger).filter_by(event="release").count() == 1      # 额度退回


def test_stop_unknown_draft_is_ignored(env):
    drafts_mod = mod("experience.drafts")
    run(drafts_mod.on_stop_generation(SimpleNamespace(api_kwargs={"stopped_message_generation": {"chat": {"id": 7}, "draft_id": 424242}}), SimpleNamespace()))
    run(drafts_mod.on_stop_generation(SimpleNamespace(api_kwargs={}), SimpleNamespace()))


# ---------- 能力缓存 / 探测 ----------
def test_capability_probe_results_respected(env):
    caps, probe = mod("experience.capabilities"), mod("experience.probe")
    assert caps.ok("business", "effect") is False and caps.ok("business", "pin") is False      # 未证实的 Business 能力默认关闭
    assert caps.ok("bot", "effect") is True
    bot = SimBot()
    lines = run(probe.run_probe(bot, 7))
    assert any("特效 🎉" in l for l in lines) and caps.ok("bot", "pin") is True and caps.ok("bot", "draft") is True
    caps.set_probe_result("bot", "react", False)
    assert mod("experience.reactions").enabled("bot") is False
    caps.set_probe_result("business", "pin", True)
    assert caps.ok("business", "pin") is True


def test_probe_command_owner_only(env):
    probe = mod("experience.probe")
    sent = []

    async def reply(text, **kw):
        sent.append(text)
    upd = SimpleNamespace(effective_user=SimpleNamespace(id=999), effective_chat=SimpleNamespace(id=999), message=SimpleNamespace(reply_text=reply))
    run(probe.cmd_probe(upd, SimpleNamespace(bot=SimBot())))
    assert "没有这个权限" in sent[-1]
    upd2 = SimpleNamespace(effective_user=SimpleNamespace(id=1), effective_chat=SimpleNamespace(id=1), message=SimpleNamespace(reply_text=reply))
    run(probe.cmd_probe(upd2, SimpleNamespace(bot=SimBot())))
    assert "探测结果" in sent[-1]


def test_ui_registry_roundtrip(env):
    reg = mod("experience.ui_registry")
    a = reg.record(7, 7, 10, "intake")
    b = reg.record(7, 7, 11, "intake")
    assert [u["id"] for u in reg.live(7, "intake")] == [a, b]
    assert reg.latest(7, "intake")["id"] == b and reg.by_message(7, 10)["id"] == a
    reg.set_state(a, "void")
    assert [u["id"] for u in reg.live(7, "intake")] == [b]
