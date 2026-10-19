"""真机反馈修复:① 新客户一进私聊就在管理群建话题(含超时重试、点按钮也触发、管理员不建);
② 管理后台可设置 USDT 价格与类目,USDT 商品报价走 GMPay,无权限时明确拒绝。"""
import asyncio
import importlib
from types import SimpleNamespace

import pytest
from telegram.error import TimedOut

from sim import Sim, SimBot
from test_wp8_gmpay import gw  # noqa: F401  (fixture)

PKG = "interactive-bot"


def run(c):
    return asyncio.run(c)


class TopicBot(SimBot):
    def __init__(self, fail_first=0, always_fail=False):
        super().__init__()
        self.topics, self.fail_first, self.always_fail = [], fail_first, always_fail

    async def create_forum_topic(self, chat_id, name, icon_color=None, **kw):
        if self.always_fail or self.fail_first > 0:
            self.fail_first -= 1
            raise TimedOut("Timed out")
        self.topics.append((chat_id, name))
        return SimpleNamespace(message_thread_id=500 + len(self.topics))


def thread_of(env, uid):
    with env.db.SessionMaker() as s:
        u = s.query(env.model.User).filter_by(user_id=uid).first()
        return u.message_thread_id if u else None


# ---------- ① 话题 ----------
def test_start_creates_topic_for_new_customer(env):
    s = Sim(env, uid=4001)
    s.bot = TopicBot()
    s.start()
    assert thread_of(env, 4001) == 501 and len(s.bot.topics) == 1
    assert "4001" in s.bot.topics[0][1]
    s.start()                                                         # 再次 /start 不重复开话题
    assert len(s.bot.topics) == 1


def test_topic_creation_retries_on_timeout(env):
    s = Sim(env, uid=4002)
    s.bot = TopicBot(fail_first=2)                                    # 前两次超时,第三次成功
    s.start()
    assert thread_of(env, 4002) == 501


def test_topic_failure_is_retried_on_next_interaction(env):
    s = Sim(env, uid=4003)
    s.bot = TopicBot(always_fail=True)
    s.start()                                                         # 全部失败:客户端不受影响,稍后重试
    assert thread_of(env, 4003) in (None, 0) and s.outs                # 仍收到欢迎卡
    s.bot.always_fail = False
    s.press("fd:menu")                                                # 之后任何一次互动(点按钮)都会补建
    assert thread_of(env, 4003) == 501


def test_button_click_creates_topic_without_any_text(env):
    s = Sim(env, uid=4004)
    s.bot = TopicBot()
    s.press("fd:go:software")
    assert thread_of(env, 4004) == 501


def test_admin_start_does_not_create_topic(env):
    uh = importlib.import_module(f"{PKG}.handlers.user_handlers")
    bot = TopicBot()
    ctx = SimpleNamespace(bot=bot, user_data={}, bot_data={})
    admin = SimpleNamespace(id=1, first_name="a", last_name=None, username=None, full_name="a")       # 1 号是管理员
    assert run(uh.ensure_topic_for_customer(admin, ctx)) == 0 and not bot.topics
    customer = SimpleNamespace(id=4005, first_name="c", last_name=None, username=None, full_name="c")
    assert run(uh.ensure_topic_for_customer(customer, ctx)) == 501


# ---------- ② 商品价格与类目 ----------
def test_parse_sku_pipe_format_with_category_and_usdt(env):
    w = importlib.import_module(f"{PKG}.admin.wizards")
    sku, item = w.parse_sku("bot_basic|基础客服机器人|Basic bot|288|bot_custom|同事今天确认", ["bot_custom", "software"])
    assert sku == "bot_basic" and item == {"title_zh": "基础客服机器人", "title_en": "Basic bot", "usdt": "288",
                                          "category": "bot_custom", "next_step": "同事今天确认"}
    assert w.parse_sku("a1|名字||88.5|software", ["software"])[1]["usdt"] == "88.5"          # 英文名可省
    for bad, hint in (("a1|名|N|abc|software", "价格"), ("a1|名|N|0|software", "价格"), ("a1|名|N|1.234|software", "价格"),
                      ("a1|名|N|10|nope", "类目"), ("A B|名|N|10|software", "编号"), ("a1|名|N|10", "至少需要")):
        with pytest.raises(w.WizardError) as e:
            w.parse_sku(bad, ["software"])
        assert hint in str(e.value)


def test_legacy_five_field_format_still_works(env):
    w = importlib.import_module(f"{PKG}.admin.wizards")
    sku, item = w.parse_sku("monthly 月度套餐 Monthly 1500 6.5")
    assert item["stars"] == 1500 and item["ton"] == 6.5


def test_sku_prompt_lists_categories(env):
    w = importlib.import_module(f"{PKG}.admin.wizards")
    text = w.sku_prompt([("software", "软件"), ("bot_custom", "机器人定制")])
    assert "USDT" in text and "software" in text and "机器人定制" in text


def test_catalog_accepts_usdt_only_and_rejects_bad_price(env):
    c = env.commerce
    c.set_catalog({"bot_basic": {"title_zh": "基础", "usdt": "288", "category": "bot_custom"}})
    assert c.get_catalog()["bot_basic"]["usdt"] == "288"
    for bad in ({"usdt": "0"}, {"usdt": "abc"}, {}):
        with pytest.raises(ValueError):
            c.set_catalog({"x": {"title_zh": "x", **bad}})


def test_shop_view_shows_usdt_and_category(env):
    env.commerce.set_catalog({"bot_basic": {"title_zh": "基础客服机器人", "usdt": "288", "category": "bot_custom"}})
    views = importlib.import_module(f"{PKG}.admin.views")
    text, _ = views.set_shop(env.commerce.get_catalog(), {})
    assert "288 USDT" in text and "bot_custom" in text
    qtext, markup = views.quote_choices(7, env.commerce.get_catalog())
    assert any("288 USDT" in b.text for row in markup.inline_keyboard for b in row)


def test_usdt_catalog_quote_goes_through_gateway(gw):
    gw.commerce.set_catalog({"bot_basic": {"title_zh": "基础客服机器人", "title_en": "Basic bot", "usdt": "288", "category": "bot_custom",
                                           "next_step": "明天确认"}})
    ch = importlib.import_module(f"{PKG}.handlers.commerce_handlers")
    bot = SimBot()
    order, ok = run(ch.send_quote(bot, 7, "bot_basic"))
    assert ok and order.base_amount_str == "288" and order.state == "quoted"
    assert any((b.callback_data or "").startswith("pay:gm:") for o in bot.outs if o["markup"] for row in o["markup"].inline_keyboard for b in row)
    order2, _ = run(ch.send_quote(bot, 7, "bot_basic"))                                  # 同 SKU 重新报价:旧的作废
    assert gw.commerce.get_order(order.id).state == "superseded" and gw.commerce.get_order(order2.id).state == "quoted"


def test_manager_cannot_publish_catalog_and_is_told(env):
    """主管没有目录发布权限:不能写进"保存了却不生效"的旧存储,要明确拒绝。"""
    console = importlib.import_module(f"{PKG}.admin.console")
    permissions = importlib.import_module(f"{PKG}.admin.permissions")
    perm_mgr = 4242
    permissions.roles = lambda: {perm_mgr: "manager", 1: "owner"}
    ctx = SimpleNamespace(user_data={console.PENDING_KEY: {"kind": "sku", "payload": ("p1", {"title_zh": "p", "usdt": "10", "category": "software"})}}, bot_data={})
    shown = []

    async def fake_show(query, rendered):
        shown.append(rendered)
    console._show = fake_show
    note = run(console._apply_pending(SimpleNamespace(), ctx, perm_mgr))
    assert "业主" in note and "p1" not in env.commerce.get_catalog()
    ctx2 = SimpleNamespace(user_data={console.PENDING_KEY: {"kind": "sku", "payload": ("p1", {"title_zh": "p", "usdt": "10", "category": "software"})}}, bot_data={})
    run(console._apply_pending(SimpleNamespace(), ctx2, 1))                                # 业主可以保存
    assert env.commerce.get_catalog()["p1"]["usdt"] == "10"
