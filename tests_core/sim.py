"""对话模拟器(05 §8):真实 handler/gate/ai_turn + 假 Bot/假 LLM,用于黄金对话回归。"""
from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace

PKG = "interactive-bot"


class Mem:
    def __init__(self):
        self.h = []

    def add(self, uid, role, content):
        self.h.append({"role": role, "content": content})

    def get_history(self, uid):
        return list(self.h)

    def get_user_summary(self, uid, q):
        return ""

    def store(self, uid, t):
        pass


class SimLLM:
    def __init__(self, reply="好的,我来帮您看看。", exc=None):
        self.reply, self.exc, self.calls, self.requests = reply, exc, 0, []

    async def chat(self, **kw):
        self.calls += 1
        self.requests.append(kw)
        if self.exc:
            raise self.exc
        return self.reply

    @property
    def last_system(self) -> str:
        return self.requests[-1]["system_prompt"] if self.requests else ""

    @property
    def last_messages(self) -> list:
        return self.requests[-1]["messages"] if self.requests else []


class SimBot:
    def __init__(self):
        self.outs: list[dict] = []
        self.calls: list[tuple] = []          # (method, kwargs) 其他 API 调用(pin/反应/草稿…)
        self.username = "baimao_interactive_bot"

    async def send_message(self, **kw):
        self.outs.append({"kind": "send", "text": kw.get("text", ""), "markup": kw.get("reply_markup"),
                          "biz": kw.get("business_connection_id"), "effect": kw.get("message_effect_id"), "kw": kw})
        return SimpleNamespace(message_id=len(self.outs))

    async def send_chat_action(self, **kw):
        pass

    async def do_api_request(self, method, api_kwargs=None, **kw):
        self.calls.append((method, api_kwargs or {}))
        return True

    # 体验层用到的其他 API:全部只做记录
    async def edit_message_text(self, **kw):
        self.calls.append(("edit_message_text", kw))
        return True

    async def edit_message_reply_markup(self, **kw):
        self.calls.append(("edit_message_reply_markup", kw))
        return True

    async def pin_chat_message(self, **kw):
        self.calls.append(("pin_chat_message", kw))
        return True

    async def unpin_chat_message(self, **kw):
        self.calls.append(("unpin_chat_message", kw))
        return True

    async def delete_message(self, **kw):
        self.calls.append(("delete_message", kw))
        return True

    async def set_message_reaction(self, **kw):
        self.calls.append(("set_message_reaction", kw))
        return True

    def api_calls(self, name):
        return [kw for n, kw in self.calls if n == name]


class FakeQuery:
    def __init__(self, bot, data, uid, biz=None):
        self.data, self.bot = data, bot
        self.from_user = SimpleNamespace(id=uid, first_name="t", full_name="t")
        self.message = SimpleNamespace(chat_id=uid, message_thread_id=None, business_connection_id=biz)
        self.answers = []

    async def answer(self, text="", show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text=None, reply_markup=None, parse_mode=None, **kw):
        self.bot.outs.append({"kind": "edit", "text": text, "markup": reply_markup, "biz": None, "effect": None, "kw": kw})

    def get_bot(self):
        return self.bot


class Sim:
    def __init__(self, env, channel="bot", lang="zh", uid=7, llm=None):
        self.env, self.channel, self.uid = env, channel, uid
        self.biz = "biz-conn-1" if channel == "business" else None
        self.bot = SimBot()
        self.llm = llm or SimLLM()
        self.mem = importlib.import_module(f"{PKG}.memory.conversation_store").ConversationStore(max_messages=15)   # 与线上一致:落库的对话记忆
        self.lm = Mem()
        self.last_gate = None
        if lang == "en":
            env.runtime.lang_for(uid, "hello there")
        self.first_turns: list = []

        async def first_turn(bot, user_id, chat_id, texts, biz_conn_id=None):
            self.first_turns.append(list(texts))
            await env.ai_turn.run_ai_turn(bot=self.bot, user_id=user_id, chat_id=chat_id, texts=texts, llm=self.llm,
                                          short_memory=self.mem, long_memory=self.lm, biz_conn_id=biz_conn_id)
            return True
        env.ai_turn.run_first_turn = first_turn

    # ----- 输出视图 -----
    @property
    def outs(self) -> list[dict]:
        return self.bot.outs

    def since(self, mark: int) -> list[dict]:
        return self.bot.outs[mark:]

    @property
    def texts(self) -> str:
        return "\n".join(o["text"] or "" for o in self.bot.outs)

    def buttons(self, out: dict | None = None) -> list:
        out = out or next((o for o in reversed(self.bot.outs) if o["markup"]), None)
        if not out or not out["markup"]:
            return []
        return [b for row in out["markup"].inline_keyboard for b in row]

    # ----- 动作 -----
    def start(self, param: str | None = None) -> list[dict]:
        cmd = importlib.import_module(f"{PKG}.handlers.command_handlers")
        mark = len(self.outs)
        user = SimpleNamespace(id=self.uid, first_name="t", last_name=None, username=None, full_name="t")
        upd = SimpleNamespace(effective_user=user, effective_chat=SimpleNamespace(id=self.uid), message=None)
        ctx = SimpleNamespace(bot=self.bot, user_data={}, bot_data={}, args=[param] if param else [])
        asyncio.run(cmd.start(upd, ctx))
        return self.since(mark)

    def say(self, text: str) -> list[dict]:
        env = self.env
        mark = len(self.outs)
        env.service.record_message(self.uid, "in", text, channel=self.channel)
        if env.service.is_human_locked(self.uid):                      # 与 handlers 一致:人工锁中不进入前台,只回"处理中"卡
            router = importlib.import_module(f"{PKG}.frontdesk.router")
            asyncio.run(router.notify_pending(self.bot, self.uid, self.uid, self.biz))
            return self.since(mark)
        gate = asyncio.run(env.gate.admit(self.bot, self.uid, self.uid, text, channel=self.channel, biz_conn_id=self.biz))
        self.last_gate = gate
        self._run_gate(gate)
        return self.since(mark)

    def _run_gate(self, gate):
        if gate.ai and gate.immediate:
            self.first_turns.append(list(gate.texts))
            asyncio.run(self.env.ai_turn.run_ai_turn(
                bot=self.bot, user_id=self.uid, chat_id=self.uid, texts=gate.texts, llm=self.llm,
                short_memory=self.mem, long_memory=self.lm, biz_conn_id=self.biz))
        elif gate.ai:
            asyncio.run(self.env.ai_turn.run_ai_turn(
                bot=self.bot, user_id=self.uid, chat_id=self.uid, texts=gate.texts, llm=self.llm,
                short_memory=self.mem, long_memory=self.lm, biz_conn_id=self.biz))

    def click(self, label_or_data: str) -> list[dict]:
        btn = next((b for b in self.buttons() if label_or_data in (b.text or "") or label_or_data == b.callback_data), None)
        assert btn is not None, f"没有找到按钮 {label_or_data!r};当前按钮:{[b.text for b in self.buttons()]}"
        return self.press(btn.callback_data)

    def press(self, data: str) -> list[dict]:
        cb = importlib.import_module(f"{PKG}.handlers.callback_handlers")
        mark = len(self.outs)
        q = FakeQuery(self.bot, data, self.uid, self.biz)
        ctx = SimpleNamespace(bot=self.bot, user_data={}, bot_data={})
        upd = SimpleNamespace(callback_query=q)
        fn = cb.callback_fd if data.startswith("fd:") else cb.callback_frontdesk
        asyncio.run(fn(upd, ctx))
        self.last_query = q
        return self.since(mark)

    def advance(self, hours: float = 0, minutes: float = 0, days: float = 0) -> None:
        self.env.runtime.shift_time(hours=hours, minutes=minutes, days=days)

    # ----- 状态视图 -----
    @property
    def case(self):
        with self.env.db.SessionMaker() as s:
            c = s.query(self.env.model.FrontdeskCase).filter_by(user_id=self.uid).order_by(self.env.model.FrontdeskCase.id.desc()).first()
            if c:
                s.expunge(c)
            return c

    @property
    def answers(self) -> dict:
        c = self.case
        return json.loads(c.answers_json or "{}") if c else {}

    @property
    def llm_calls(self) -> int:
        return self.llm.calls

    def outbox(self) -> list:
        with self.env.db.SessionMaker() as s:
            return [r.text for r in s.query(self.env.model.Outbox).all()]


# ----- 断言辅助 -----
def assert_no_words(outs_or_text, words):
    text = outs_or_text if isinstance(outs_or_text, str) else "\n".join(o["text"] or "" for o in outs_or_text)
    bad = [w for w in words if w in text]
    assert not bad, f"出现了不应出现的词 {bad}:\n{text}"


def assert_buttons(out: dict, expect: list[str]):
    labels = [b.text for row in (out["markup"].inline_keyboard if out["markup"] else []) for b in row]
    for e in expect:
        assert any(e in l for l in labels), f"缺少按钮 {e!r};现有:{labels}"
