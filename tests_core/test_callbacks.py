"""前台按钮回调:真实回调函数 + 假 Query,覆盖点击→作答→跳过→完成→首轮 AI 的整条路径。"""
import asyncio
import importlib

import pytest

from test_integration import Bot, LLM


class FakeQuery:
    def __init__(self, data, uid=7):
        self.data = data
        self.from_user = type("U", (), {"id": uid, "first_name": "t", "full_name": "t"})()
        self.message = type("M", (), {"chat_id": uid, "message_thread_id": None, "business_connection_id": None})()
        self.edits, self.answers = [], []

    async def answer(self, text="", show_alert=False):
        self.answers.append(text)

    async def edit_message_text(self, text=None, reply_markup=None, parse_mode=None, **kw):
        self.edits.append((text, reply_markup))

    def get_bot(self):
        return self.bot


def click(env, data, uid=7):
    cb = importlib.import_module("interactive-bot.handlers.callback_handlers")
    q = FakeQuery(data, uid)
    bot = Bot()
    q.bot = bot
    ctx = type("C", (), {"bot": bot, "user_data": {}, "bot_data": {}})()
    upd = type("Upd", (), {"callback_query": q})()
    asyncio.run(cb.callback_frontdesk(upd, ctx))
    if not q.edits and bot.sent:        # 点击按钮后现在是"发新卡 + 删旧卡"(experience.button_replace),新卡内容等同于原来的编辑结果
        q.edits = [(m.get("text"), m.get("reply_markup")) for m in bot.sent]
    return q, bot


def test_welcome_buttons_all_respond(env):
    for role in ("prospect", "support", "partner", "visitor"):
        q, bot = click(env, f"frontdesk_intent:{role}:7")
        assert q.answers, f"{role}: 没有应答(Telegram 按钮会一直转圈)"
        assert q.edits, f"{role}: 没有更新卡片"


def test_full_click_path_prospect(env, monkeypatch):
    q, _ = click(env, "frontdesk_intent:prospect:7")
    assert "哪一类服务" in q.edits[-1][0]
    q, _ = click(env, "frontdesk_ans:7:line:bot_custom")
    assert "想让机器人帮您做什么" in q.edits[-1][0]
    env.precheck.accept_text(7, "做一个能自动回复客户的机器人")
    q, bot = click(env, "frontdesk_ans:7:issue:install")          # 不属于当前流程的旧卡片:无副作用
    assert q.answers and not q.edits


def test_foreign_user_cannot_press_others_button(env):
    q, _ = click(env, "frontdesk_intent:prospect:7", uid=99)
    assert not q.edits and q.answers
