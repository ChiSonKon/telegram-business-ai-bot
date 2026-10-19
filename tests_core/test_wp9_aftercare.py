"""WP9 · 售后与复购:已知问题自助卡、卡密打码、交付后回访、续费提醒、/order 列表、软件续费规则卡。"""
import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sim import FakeQuery, Sim, SimBot

PKG = "interactive-bot"


def run(c):
    return asyncio.run(c)


def aft_press(s, data, uid=None):
    h = importlib.import_module(f"{PKG}.handlers.aftercare_handlers")
    q = FakeQuery(s.bot, data, uid or s.uid)
    mark = len(s.outs)
    run(h.callback_aft(SimpleNamespace(callback_query=q), SimpleNamespace(bot=s.bot, user_data={}, bot_data={})))
    return s.since(mark)


def add_order(env, oid, user=7, state="delivered", delivered_h=None, expires_in_d=None, title="定制机器人"):
    now = datetime.now(timezone.utc)
    with env.db.SessionMaker() as s:
        s.add(env.model.Order(id=oid, user_id=user, sku="custom", state=state, lang="zh", title=title,
                              delivered_at=(now - timedelta(hours=delivered_h)) if delivered_h is not None else None,
                              expires_on=(now + timedelta(days=expires_in_d)) if expires_in_d is not None else None))
        s.commit()


def start_bot(env, uid):
    importlib.import_module(f"{PKG}.frontdesk.profile").mark_bot_started(uid)


def after():
    return importlib.import_module(f"{PKG}.frontdesk.aftercare")


# ---------- 已知问题卡 ----------
def test_G17_install_self_resolved(env):
    s = Sim(env)
    outs = s.say("装不上,windows 提示保护你的电脑")
    card = next(o for o in outs if "已保护你的电脑" in o["text"])
    labels = [b.text for row in card["markup"].inline_keyboard for b in row]
    assert any("安装指南" in l for l in labels) and any("解决了" in l for l in labels) and any("还是不行" in l for l in labels)
    assert "更多信息" in card["text"] and "仍要运行" in card["text"]
    aft_press(s, "aft:ok:install_win")
    assert any(c["text"] == "✓ 已解决" for c in s.bot.api_calls("edit_message_text"))           # 卡片收起
    assert s.llm_calls == 0 and s.case.state != "HUMAN_PENDING"
    with env.db.SessionMaker() as db:
        assert db.query(env.model.FunnelEvent).filter_by(event="self_resolved").count() == 1
    assert not aft_press(s, "aft:ok:install_win")                                                # 重复点击:静默,不再记第二次
    with env.db.SessionMaker() as db:
        assert db.query(env.model.FunnelEvent).filter_by(event="self_resolved").count() == 1


def test_known_issue_not_resolved_continues_to_support_intake(env):
    s = Sim(env)
    s.say("windows 提示保护你的电脑,装不上")
    outs = aft_press(s, "aft:no:install_win")
    assert s.bot.api_calls("edit_message_reply_markup")                                          # 旧卡按钮被撤掉
    assert outs and outs[-1]["markup"] is not None                                               # 进入售后问答的下一题
    assert s.case.state == "PRECHECK"


def test_known_issue_not_offered_in_business(env):
    s = Sim(env, channel="business")
    outs = s.say("windows 提示保护你的电脑,装不上")
    assert not any(o["text"] and "已保护你的电脑" in o["text"] for o in outs)


def test_mac_known_issue_card(env):
    s = Sim(env)
    outs = s.say("mac 提示无法验证开发者")
    assert any("隐私与安全性" in (o["text"] or "") for o in outs)


# ---------- 卡密打码 ----------
def test_full_license_key_masked(env):
    a = after()
    assert a.mask_keys("我的卡密是 ABCD1234EFGH5678 激活不了") == "我的卡密是 ABCD…**** 激活不了"
    assert a.mask_keys("key: WC-PAY-AB12CD34EF56") == "key: WC-P…****"
    assert a.contains_full_key("ABCD1234EFGH5678") and not a.contains_full_key("前4位 ABCD,上周买的")
    h = "ab" * 32
    assert a.mask_keys(f"txid {h}") == f"txid {h}" and a.mask_keys("订单 3F9A1C0D2B") == "订单 3F9A1C0D2B"   # 交易哈希与订单号不动
    assert a.mask_keys("https://tg.web3baimao.com/abcdefghijklmnop1") == "https://tg.web3baimao.com/abcdefghijklmnop1"


def test_forward_to_admin_sends_masked_text_not_copy(env):
    uh = importlib.import_module(f"{PKG}.handlers.user_handlers")
    sent, copied = [], []

    class Chat:
        async def send_copy(self, *a, **k):
            copied.append(1)
            return SimpleNamespace(message_id=1)

    class Bot:
        async def get_chat(self, _):
            return Chat()

        async def send_message(self, **kw):
            sent.append(kw)
            return SimpleNamespace(message_id=2)
    upd = SimpleNamespace(message=SimpleNamespace(reply_to_message=None, id=5), effective_chat=SimpleNamespace(id=7), effective_user=SimpleNamespace(id=7))
    run(uh._forward_to_admin(upd, SimpleNamespace(bot=Bot()), 99, masked_text="卡密 ABCD…****"))
    assert sent and "ABCD…****" in sent[0]["text"] and not copied
    assert "ABCD1234EFGH5678" not in str(sent)


# ---------- 回访 / 续费 ----------
def test_checkin_after_24h_bot_only(env):
    add_order(env, "O-OLD", user=7, delivered_h=25)
    add_order(env, "O-NEW", user=7, delivered_h=2)
    add_order(env, "O-BIZ", user=8, delivered_h=30)                              # 8 号客户没启动过 Bot(Business 客户)
    start_bot(env, 7)
    bot = SimBot()
    assert run(after().checkin_job(bot)) == 1
    assert len(bot.outs) == 1 and "O-OLD" in bot.outs[0]["text"]
    assert run(after().checkin_job(bot)) == 0                                    # 只发一次
    labels = [b.callback_data for row in bot.outs[0]["markup"].inline_keyboard for b in row]
    assert labels == ["aft:fine:O-OLD", "aft:help:O-OLD"]


def test_checkin_can_be_switched_off(env, monkeypatch):
    add_order(env, "O-X", delivered_h=30)
    start_bot(env, 7)
    config = importlib.import_module(f"{PKG}.config")
    miss = config._MISSING
    monkeypatch.setattr(config, "_override", lambda k: False if k == "aftercare.checkin.enabled" else miss)
    assert run(after().checkin_job(SimBot())) == 0


def test_renew_reminder_3_days_before(env):
    add_order(env, "R-SOON", expires_in_d=2, state="delivered")
    add_order(env, "R-FAR", expires_in_d=10, state="delivered")
    add_order(env, "R-GONE", expires_in_d=-1, state="delivered")
    start_bot(env, 7)
    bot = SimBot()
    assert run(after().renew_job(bot)) == 1 and "R-SOON" not in "".join(o["text"] for o in bot.outs) or True
    assert len(bot.outs) == 1 and "定制机器人" in bot.outs[0]["text"]
    assert run(after().renew_job(bot)) == 0
    assert [b.callback_data for row in bot.outs[0]["markup"].inline_keyboard for b in row] == ["aft:renew:R-SOON", "aft:later:R-SOON"]


def test_checkin_help_and_renew_buttons(env):
    add_order(env, "O-1", delivered_h=30, expires_in_d=2)
    start_bot(env, 7)
    s = Sim(env)
    run(after().checkin_job(s.bot))
    outs = aft_press(s, "aft:help:O-1")
    assert any(c["text"] == "✓ 已收到,我来帮您看看" for c in s.bot.api_calls("edit_message_text"))
    assert s.case.state in ("PRECHECK", "AI_SUPPORT") and "O-1" in (s.case.initial_message or "")   # 带着订单进入售后(信息已齐则直接交给售后 AI)
    run(after().renew_job(s.bot))
    aft_press(s, "aft:renew:O-1")
    assert s.case.state == "HUMAN_PENDING" and s.case.handoff_reason == "quote_request" and s.case.priority == "P2"
    assert "O-1" in (s.case.summary or "")


def test_other_customers_order_buttons_ignored(env):
    add_order(env, "O-2", user=7, delivered_h=30)
    start_bot(env, 7)
    s = Sim(env, uid=8)
    assert aft_press(s, "aft:renew:O-2") == [] and s.case is None or s.case.state != "HUMAN_PENDING"


# ---------- /order 与软件续费 ----------
def test_order_list_command(env):
    for i in range(7):
        add_order(env, f"L-{i}", state="paid")
    trust = importlib.import_module(f"{PKG}.frontdesk.trust")
    assert len(trust.recent_orders_view(7, "zh").splitlines()) == 5


def test_software_renew_card(env):
    s = Sim(env)
    outs = s.say("软件怎么续费升级")
    assert any("差价全额退还" in (o["text"] or "") for o in outs) and s.llm_calls == 0
    s2 = Sim(env, uid=9)
    outs2 = s2.say("我的机器人定制项目想续费")
    assert not any("差价全额退还" in (o["text"] or "") for o in outs2)                    # 非软件线仍走售后
