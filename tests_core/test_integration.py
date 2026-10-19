"""AI 回合网关集成测试:真实 SQLite(临时目录)+ 假 Bot / 假 LLM。"""
import asyncio

import pytest
class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kw):
        self.sent.append(kw)
        return type("M", (), {"message_id": len(self.sent)})()

    async def send_chat_action(self, **kw):
        pass


class Mem:
    def __init__(self): self.h = []
    def add(self, uid, role, content): self.h.append({"role": role, "content": content})
    def get_history(self, uid): return list(self.h)
    def get_user_summary(self, uid, q): return ""
    def store(self, uid, t): pass


class LLM:
    def __init__(self, reply="好的,月度套餐 29 U,您主要用来做什么?", exc=None, gate=None):
        self.reply, self.exc, self.gate, self.calls = reply, exc, gate, 0

    async def chat(self, **kw):
        self.calls += 1
        if self.gate:
            await self.gate.wait()
        if self.exc:
            raise self.exc
        return self.reply


def turn(env, bot, llm, ticket=None, text="买软件"):
    return env.ai_turn.run_ai_turn(bot=bot, user_id=7, chat_id=7, texts=[text], llm=llm,
                                   short_memory=Mem(), long_memory=Mem(), ticket=ticket)


def ledger(env, event):
    with env.db.SessionMaker() as s:
        return s.query(env.model.AiUsageLedger).filter_by(event=event).count()


def setup_role(env, role="prospect"):
    env.service.set_intent(7, "x", role=role)


def test_happy_path_settles_success(env):
    setup_role(env); bot = Bot()
    assert asyncio.run(turn(env, bot, LLM()))
    assert len(bot.sent) == 1 and ledger(env, "success") == 1


def test_takeover_during_inference_discards_reply(env):
    setup_role(env); bot = Bot()

    async def scenario():
        gate = asyncio.Event(); llm = LLM(gate=gate)
        task = asyncio.create_task(turn(env, bot, llm))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        env.service.admin_takeover(7)          # 管理员接管 → bump 版本号
        gate.set()
        return await task

    assert asyncio.run(scenario()) is False
    assert bot.sent == [] and ledger(env, "success") == 0


def test_guardrail_blocks_unauthorized_amount_and_hands_off(env):
    setup_role(env); bot = Bot()
    assert not asyncio.run(turn(env, bot, LLM(reply="给您内部价 1 U,直接转我 " + "UQ" + "a" * 46)))
    assert env.service.is_human_locked(7)
    assert len(bot.sent) == 1 and "请同事来确认" in bot.sent[0]["text"]     # 只发了"请同事确认"提示(03 §4.12 新文案)


def test_quota_exhaustion_then_human(env):
    setup_role(env, "prospect"); bot = Bot()                              # WP5:support 剧本 2 轮后会主动交给同事,额度测试改用销售角色
    limit = env.service.ROLE_LIMITS["prospect"]
    for _ in range(limit):
        assert asyncio.run(turn(env, bot, LLM()))
    llm = LLM()
    assert not asyncio.run(turn(env, bot, llm))
    assert llm.calls == 0 and env.service.is_human_locked(7)


def test_breaker_opens_after_failures(env):
    setup_role(env); bot = Bot(); llm = LLM(exc=RuntimeError("boom"))
    for _ in range(3):
        env.service.admin_release(7)
        env.db.SessionMaker  # noqa
        with env.db.SessionMaker() as s:
            c = s.query(env.model.FrontdeskCase).first(); c.state = "AI_SALES"; s.commit()
            u = s.query(env.model.User).first(); u.chat_mode = "AI_MODE"; s.commit()
        asyncio.run(turn(env, bot, llm))
    assert env.runtime.llm_breaker.state == "open"
    assert ledger(env, "success") == 0 and ledger(env, "release") == 3


def test_english_customer_gets_english_cards(env):
    bot = Bot()
    g = asyncio.run(env.gate.admit(bot, 7, 7, "hello there", channel="bot"))
    assert not g.ai and "Welcome to WhiteCat Studio" in bot.sent[0]["text"]


def test_sender_stops_between_bubbles(env):
    bot = Bot(); calls = {"n": 0}

    def alive():
        calls["n"] += 1
        return calls["n"] <= 3      # 语音检查、第一个气泡前后通过,第二个气泡前失效

    text = ("甲" * 100 + "\n\n" + "乙" * 100)
    sent = asyncio.run(env.sender.send_humanlike_messages(bot, 7, text, should_continue=alive))
    assert len(sent) == 1


def test_handoff_enqueues_admin_note_once_and_outbox_retries(env):
    import importlib
    outbox = importlib.import_module("interactive-bot.outbox")
    setup_role(env)
    env.service.handoff(7, "explicit_human")
    env.service.handoff(7, "explicit_human")                 # 同一事件重复触发:只入队一次
    with env.db.SessionMaker() as s:
        assert s.query(env.model.Outbox).count() == 1

    class Down(Bot):
        async def send_message(self, **kw):
            raise RuntimeError("admin group unreachable")

    clock = [1000.0]
    assert asyncio.run(outbox.drain(Down(), clock=lambda: clock[0])) == 0
    with env.db.SessionMaker() as s:
        row = s.query(env.model.Outbox).one()
        assert row.attempts == 1 and not row.sent and row.next_try > 1000
    assert asyncio.run(outbox.drain(Bot(), clock=lambda: clock[0])) == 0     # 退避期内不重试
    clock[0] += 3600
    ok = Bot()
    assert asyncio.run(outbox.drain(ok, clock=lambda: clock[0])) == 1 and "需要跟进" in ok.sent[0]["text"]
    assert asyncio.run(outbox.drain(Bot(), clock=lambda: clock[0] + 9999)) == 0   # 已发送不再重发
