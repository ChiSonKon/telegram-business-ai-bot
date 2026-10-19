"""WP8 · 客户/管理员流程:自定义报价 → 选网络 → 订单卡(置顶)→ 自动确认 / 提交交易哈希 / 网关不可用降级。
真实处理器 + SimBot + 模拟网关(httpx.MockTransport),不碰真实网关与资金。"""
import asyncio
import importlib
from types import SimpleNamespace

from sim import SimBot
from test_wp8_gmpay import gw  # noqa: F401  (fixture)


def run(c):
    return asyncio.run(c)


def H(name):
    return importlib.import_module(f"interactive-bot.{name}")


class Msg:
    def __init__(self, text, replies):
        self.text, self.replies = text, replies

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw))


def ctx(bot):
    return SimpleNamespace(bot=bot, user_data={}, bot_data={})


def admin_cmd(text, uid=1):
    replies = []
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid), message=Msg(text, replies)), replies


class Q:
    def __init__(self, data, uid):
        self.data, self.from_user = data, SimpleNamespace(id=uid, full_name="t")
        self.message = SimpleNamespace(chat_id=uid, reply_text=self._reply)
        self.answers, self.edits, self.replies = [], [], []

    async def answer(self, text="", show_alert=False):
        self.answers.append(text)

    async def edit_message_text(self, text=None, **kw):
        self.edits.append(text)

    async def _reply(self, text, **kw):
        self.replies.append(text)


def click(fn, data, uid, bot, c=None):
    q = Q(data, uid)
    run(fn(SimpleNamespace(callback_query=q), c or ctx(bot)))
    return q


def texts(bot):
    return [o["text"] for o in bot.outs]


def buttons(bot):
    return [b.callback_data for o in bot.outs if o["markup"] for row in o["markup"].inline_keyboard for b in row if b.callback_data]


def new_quote(bot, amount="288"):
    gh = H("handlers.gateway_handlers")
    upd, replies = admin_cmd(f"/qcustom 7 {amount} 定制机器人 | 自动回复 | 明天确认")
    run(gh.cmd_qcustom(upd, ctx(bot)))
    return gh, replies


def order_id(bot):
    return next(b for b in buttons(bot) if b.startswith("pay:gm:")).split(":")[2]


def test_qcustom_sends_network_choice_card(gw):
    bot = SimBot()
    gh, replies = new_quote(bot)
    assert any(b.startswith("pay:gm:") and b.endswith(":tron") for b in buttons(bot)) and "已发送报价" in replies[-1][0]
    assert "288" in texts(bot)[0] and "付款网络" in texts(bot)[0]


def test_choose_network_shows_address_and_pins(gw):
    bot = SimBot()
    gh, _ = new_quote(bot)
    oid = order_id(bot)
    click(gh.callback_gateway, f"pay:gm:{oid}:tron", 7, bot)
    order = gw.commerce.get_order(oid)
    assert order.state == "awaiting_payment" and order.pay_address == "TTestTronAddress001"
    edits = bot.api_calls("edit_message_text")
    assert edits and "TTestTronAddress001" in edits[-1]["text"] and "TRON" in edits[-1]["text"]
    assert bot.api_calls("pin_chat_message")                                   # 收款信息阶段置顶


def test_wrong_customer_cannot_use_buttons(gw):
    bot = SimBot()
    gh, _ = new_quote(bot)
    oid = order_id(bot)
    q = click(gh.callback_gateway, f"pay:gm:{oid}:tron", 999, bot)
    assert gw.commerce.get_order(oid).state == "quoted" and q.answers


def test_poll_marks_paid_notifies_and_updates_card(gw):
    bot = SimBot()
    gh, _ = new_quote(bot)
    oid = order_id(bot)
    click(gh.callback_gateway, f"pay:gm:{oid}:tron", 7, bot)
    gw.state["status"][gw.commerce.get_order(oid).gm_trade_id] = 2

    async def checker(t):
        return await gw.gmpay.check_status(t, client=gw.client)
    assert run(gw.gmon.poll_gmpay(bot, checker)) == 1
    assert gw.commerce.get_order(oid).state == "paid"
    paid_msgs = [o for o in bot.outs if "已收到您的付款" in o["text"]]
    assert paid_msgs and "明天确认" in paid_msgs[0]["text"]
    assert any("已收款" in c["text"] for c in bot.api_calls("edit_message_text"))      # 订单卡原位更新为状态行


def test_txid_unconfirmed_goes_to_review_and_alerts_admin(gw):
    bot = SimBot()
    gh, _ = new_quote(bot)
    oid = order_id(bot)
    click(gh.callback_gateway, f"pay:gm:{oid}:tron", 7, bot)
    c = ctx(bot)
    click(gh.callback_gateway, f"pay:txid:{oid}", 7, bot, c)
    assert c.user_data["await_txid"] == oid
    upd = SimpleNamespace(effective_user=SimpleNamespace(id=7), message=None)
    assert run(gh.maybe_txid(upd, c, "不是哈希")) is True and any("64 位" in t for t in texts(bot))
    txid = "ab" * 32
    assert run(gh.maybe_txid(upd, c, txid)) is True
    assert gw.commerce.get_order(oid).state == "payment_review"
    assert any(f"tronscan.org/#/transaction/{txid}" in t for t in texts(bot))             # 管理群收到链接
    assert any("正在核对" in t for t in texts(bot))
    assert bot.api_calls("delete_message")                                                # 临时"核对中"消息被删除


def test_txid_when_gateway_already_paid_settles(gw):
    bot = SimBot()
    gh, _ = new_quote(bot)
    oid = order_id(bot)
    click(gh.callback_gateway, f"pay:gm:{oid}:tron", 7, bot)
    gw.state["status"][gw.commerce.get_order(oid).gm_trade_id] = 2
    upd = SimpleNamespace(effective_user=SimpleNamespace(id=7), message=None)
    assert run(gh.maybe_txid(upd, ctx(bot), "cd" * 32)) is True                            # 直接粘贴哈希,无需先点按钮
    o = gw.commerce.get_order(oid)
    assert o.state == "paid" and o.paid_txid == "cd" * 32


def test_txid_claim_is_unique_per_hash(gw):
    bot = SimBot()
    gh, _ = new_quote(bot)
    oid = order_id(bot)
    click(gh.callback_gateway, f"pay:gm:{oid}:tron", 7, bot)
    upd = SimpleNamespace(effective_user=SimpleNamespace(id=7), message=None)
    run(gh.maybe_txid(upd, ctx(bot), "ef" * 32))
    assert gw.commerce.claim_event(f"txid:{'ef' * 32}", "OTHER", "txid", "submitted", "") is False
    assert gw.commerce.event_order(f"txid:{'ef' * 32}") == oid
    n = len(bot.outs)
    run(gh.maybe_txid(upd, ctx(bot), "ef" * 32))                                          # 同单重复提交:温和回应,不报"被其他订单占用"
    assert len(bot.outs) == n + 1 and "其他订单" not in bot.outs[-1]["text"]


def test_gateway_unconfigured_degrades_to_human(gw, monkeypatch):
    monkeypatch.delenv("GMPAY_PID")
    bot = SimBot()
    gh, replies = new_quote(bot)
    assert any("付款信息由同事提供" in t for t in texts(bot)) and "未能送达" in replies[-1][0]
    assert len(texts(bot)) >= 2                                                          # 另有管理群提醒


def test_large_quote_needs_confirmation(gw):
    bot = SimBot()
    gh = H("handlers.gateway_handlers")
    c = ctx(bot)
    upd, replies = admin_cmd("/qcustom 7 600 大单 | 详情 | 下一步")
    run(gh.cmd_qcustom(upd, c))
    assert not bot.outs and "请确认报价" in replies[0][0]
    token = next(iter(c.bot_data["pending_quotes"]))
    assert click(gh.callback_quote_confirm, f"qc:ok:{token}", 2, bot, c).answers[0].startswith("已失效")   # 别人不能代点
    click(gh.callback_quote_confirm, f"qc:ok:{token}", 1, bot, c)
    assert bot.outs and any(b.startswith("pay:gm:") for b in buttons(bot))


def test_non_admin_cannot_quote(gw):
    bot = SimBot()
    gh = H("handlers.gateway_handlers")
    upd, replies = admin_cmd("/qcustom 7 10 x", uid=555)
    run(gh.cmd_qcustom(upd, ctx(bot)))
    assert not bot.outs and not replies


def test_two_custom_quotes_stay_independent(gw):
    bot = SimBot()
    new_quote(bot, "100")
    new_quote(bot, "200")
    with gw.db.SessionMaker() as s:
        states = [o.state for o in s.query(gw.model.Order).filter(gw.model.Order.user_id == 7).all()]
    assert states == ["quoted", "quoted"]
