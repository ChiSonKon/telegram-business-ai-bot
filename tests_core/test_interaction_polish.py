"""交互打磨:点赞提示(点一下后自动取消)、点击按钮后"新消息 + 删旧卡"、完整卡密撤回、官方站点分工。"""
import asyncio
import importlib
from types import SimpleNamespace

from sim import FakeQuery, Sim, SimBot

PKG = "interactive-bot"


def run(c):
    return asyncio.run(c)


def mod(n):
    return importlib.import_module(f"{PKG}.{n}")


# ---------- 点赞提示:点一下 → 自动取消 ----------
def test_flash_reaction_sets_then_clears(env):
    rx = mod("experience.reactions")
    bot = SimBot()

    async def go():
        assert await rx.flash(bot, 7, 55, "👍", hold=0) is True
        await asyncio.sleep(0.05)
        await asyncio.sleep(0.05)
    run(go())
    calls = bot.api_calls("set_message_reaction")
    assert len(calls) == 2 and calls[0]["reaction"] and calls[1]["reaction"] == []          # 先点赞,后清空


def test_flash_skips_business_and_missing_ids(env):
    rx = mod("experience.reactions")
    bot = SimBot()
    assert run(rx.flash(bot, 7, 55, "👍", channel="business")) is False
    assert run(rx.flash(bot, 7, None, "👍")) is False and not bot.calls


# ---------- 点击按钮:弹新消息 + 删旧卡 ----------
class Q:
    def __init__(self, bot, msg_id=10, biz=None):
        self.bot = bot
        self.message = SimpleNamespace(chat_id=7, message_id=msg_id, business_connection_id=biz)
        self.edits = []

    def get_bot(self):
        return self.bot

    async def edit_message_text(self, text=None, **kw):
        self.edits.append(text)


def test_replace_card_sends_new_then_deletes_old_and_inherits_registry(env):
    kit, reg = mod("ui.kit"), mod("experience.ui_registry")
    bot = SimBot()
    old_ui = reg.record(7, 7, 10, "intake")
    run(kit.replace_card(Q(bot), "[[check]] 下一题", None))
    assert bot.outs and bot.outs[-1]["text"]
    assert [c["message_id"] for c in bot.api_calls("delete_message")] == [10]
    assert reg.get(old_ui)["state"] == "deleted"
    new = reg.latest(7, "intake")
    assert new and new["message_id"] == len(bot.outs)                                    # 新卡继承"进度卡"登记


def test_replace_card_falls_back_to_edit_when_send_fails_or_business(env):
    kit = mod("ui.kit")

    class FailBot(SimBot):
        async def send_message(self, **kw):
            raise RuntimeError("net")
    bot, q = FailBot(), None
    q = Q(bot)
    run(kit.replace_card(q, "hello", None))
    assert q.edits == ["hello"] and not bot.api_calls("delete_message")                  # 新卡没发出去:不删旧卡
    bot2 = SimBot()
    q2 = Q(bot2, biz="biz-1")
    run(kit.replace_card(q2, "hello", None, "biz-1"))
    assert q2.edits == ["hello"] and not bot2.outs                                         # Business:原位编辑


def test_replace_card_can_be_switched_off(env, monkeypatch):
    config = mod("config")
    miss = config._MISSING
    monkeypatch.setattr(config, "_override", lambda k: False if k == "experience.button_replace.enabled" else miss)
    bot, q = SimBot(), None
    q = Q(bot)
    run(mod("ui.kit").replace_card(q, "x", None))
    assert q.edits == ["x"] and not bot.outs


def test_customer_button_flow_replaces_cards(env):
    """真实前台回调:点来意按钮 → 新问题卡作为新消息出现,旧卡被删除。"""
    s = Sim(env)
    s.start()
    mark = len(s.outs)
    ch = mod("handlers.callback_handlers")
    q = FakeQuery(s.bot, "frontdesk_intent:prospect:7", 7)
    q.message = SimpleNamespace(chat_id=7, message_id=3, message_thread_id=None, business_connection_id=None)
    run(ch.callback_frontdesk(SimpleNamespace(callback_query=q), SimpleNamespace(bot=s.bot, user_data={}, bot_data={})))
    assert len(s.outs) > mark and any(c["message_id"] == 3 for c in s.bot.api_calls("delete_message"))


# ---------- 官方站点分工 ----------
def test_welcome_points_to_main_site_and_verify_lists_roles(env):
    copy = mod("config").copy
    assert "web3baimao.com" in copy("welcome", "zh") and "tg.web3baimao.com" not in copy("welcome", "zh")
    verify = copy("verify.list", "zh", list="x")
    assert "空投业务官网:airdrop.web3baimao.com" in verify and "获客软件(WhiteCat 5.0)官网:tg.web3baimao.com" in verify
    assert mod("ui.cards").SITE_URL == "https://web3baimao.com/"


def test_rag_gives_site_matching_the_business_line(env):
    r = mod("rag.retriever")
    soft = r.search_knowledge("怎么安装", line_id="software")
    assert "软件官网" in soft and "https://tg.web3baimao.com" in soft.split("官方入口")[1].split("其他站点")[0]
    air = r.search_knowledge("空投会员多少钱", line_id="airdrop")
    assert "空投业务官网" in air.split("官方入口")[1].split("其他站点")[0] and "airdrop.web3baimao.com" in air
    gen = r.search_knowledge("你们是做什么的")
    assert "综合业务主站" in gen.split("官方入口")[1].split("其他站点")[0]


def test_old_default_inline_button_is_migrated(env):
    ss = mod("start_settings")
    ss.set_inline_buttons([{"text": "🌐 访问军火库官网", "action": "url", "value": "https://airdrop.web3baimao.com/", "color": "transparent",
                            "emoji_id": "", "row": 1}])
    out = ss.get_inline_buttons()
    assert out[0]["value"] == "https://web3baimao.com/" and "军火库" not in out[0]["text"]
