"""WP8 · GMPay 收款通道:签名、下单、查单轮询、回调验签、幂等、迟到/少付、自定义报价互不作废。
网关用 httpx.MockTransport 模拟(并校验请求签名),不碰真实网关与真实资金。"""
import asyncio
import json

import httpx
import pytest

from test_integration import Bot

PID, SECRET = "2001", "unit-test-secret"


@pytest.fixture()
def gw(env, monkeypatch):
    monkeypatch.setenv("GMPAY_PID", PID)
    monkeypatch.setenv("GMPAY_SECRET", SECRET)
    monkeypatch.setenv("GMPAY_BASE", "https://gw.test")
    env.gmpay = __import__("importlib").import_module("interactive-bot.commerce.providers.gmpay")
    env.gmon = __import__("importlib").import_module("interactive-bot.commerce.gmpay_monitor")
    state = {"created": [], "status": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/order/create-transaction"):
            body = json.loads(request.content)
            ok = body["signature"] == env.gmpay.sign(body, SECRET)
            if not ok:
                return httpx.Response(200, json={"status_code": 401, "message": "bad signature"})
            state["created"].append(body)
            n = len(state["created"])
            return httpx.Response(200, json={"status_code": 200, "message": "success", "data": {
                "trade_id": f"T{n:04d}", "order_id": body["order_id"], "amount": body["amount"], "currency": "USD",
                "actual_amount": body["amount"] - 0.03 * n + 0.03 * n, "receive_address": "TTestTronAddress001",
                "token": body["token"].upper(), "status": 1, "expiration_time": 9999999999, "payment_url": f"https://gw.test/pay/T{n:04d}"}})
        if "/pay/check-status/" in request.url.path:
            tid = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"status_code": 200, "data": {"trade_id": tid, "status": state["status"].get(tid, 1)}})
        if request.url.path.endswith("/config"):
            return httpx.Response(200, json={"status_code": 200, "data": {"supported_assets": [
                {"network": "tron", "display_name": "TRON", "tokens": ["USDT"]}, {"network": "empty", "tokens": []}]}})
        return httpx.Response(404)

    real = httpx.AsyncClient
    env.client = real(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))   # 处理器内部自建客户端也走模拟网关
    env.state = state
    return env


def run(coro):
    return asyncio.run(coro)


def quote(env, amount="100", user=7):
    return env.commerce.create_custom_quote(user, title="定制机器人", detail="自动回复", amount=amount, next_step="明天确认", created_by=1)


def test_sign_ignores_empty_and_signature_and_key_order(gw):
    a = gw.gmpay.sign({"b": 2, "a": "x", "c": "", "signature": "zzz", "d": None}, "k")
    b = gw.gmpay.sign({"a": "x", "b": 2}, "k")
    assert a == b and len(a) == 64
    assert gw.gmpay.sign({"a": 1.0}, "k") == gw.gmpay.sign({"a": 1}, "k")        # 整数值浮点与整数同形


def test_verify_callback_rejects_forged_and_foreign_pid(gw):
    cb = {"pid": PID, "trade_id": "T1", "order_id": "O", "amount": 100, "actual_amount": 100, "status": 2}
    cb["signature"] = gw.gmpay.sign(cb, SECRET)
    assert gw.gmpay.verify_callback(cb)
    assert not gw.gmpay.verify_callback(dict(cb, signature="0" * 64))
    assert not gw.gmpay.verify_callback(dict(cb, actual_amount=1))               # 篡改金额
    other = dict(cb, pid="1000")
    other["signature"] = gw.gmpay.sign(other, SECRET)
    assert not gw.gmpay.verify_callback(other)                                    # 别的商户


def test_supported_assets_filters_empty(gw):
    assets = run(gw.gmpay.supported_assets(client=gw.client))
    assert [a["network"] for a in assets] == ["tron"]


def test_custom_quotes_do_not_supersede_each_other_but_same_sku_does(gw):
    a, b = quote(gw), quote(gw, "50")
    assert gw.commerce.get_order(a.id).state == "quoted" and gw.commerce.get_order(b.id).state == "quoted"
    gw.commerce.set_catalog({"m": {"title_zh": "月度", "stars": 100}})
    s1 = gw.commerce.create_quote(7, "m")
    s2 = gw.commerce.create_quote(7, "m")
    assert gw.commerce.get_order(s1.id).state == "superseded" and gw.commerce.get_order(s2.id).state == "quoted"
    assert gw.commerce.get_order(a.id).state == "quoted"                          # 回归缺陷:不再误伤其他订单


def test_parse_amount_rules(gw):
    assert str(gw.commerce.parse_amount("288 USDT")) == "288" and str(gw.commerce.parse_amount("1,000.5")) == "1000.5"
    for bad in ("0", "-5", "abc", "0.005", "1.234", ""):
        with pytest.raises(ValueError):
            gw.commerce.parse_amount(bad)


def test_attach_gateway_then_poll_marks_paid_once(gw):
    o = quote(gw)
    o = run(gw.commerce.attach_gateway(o.id, "tron", "usdt", client=gw.client))
    assert o.state == "awaiting_payment" and o.gm_trade_id == "T0001" and o.pay_address == "TTestTronAddress001"
    assert gw.state["created"][0]["order_id"] == o.id and gw.state["created"][0]["currency"] == "usd"
    again = run(gw.commerce.attach_gateway(o.id, "tron", "usdt", client=gw.client))   # 重复点击复用
    assert again.gm_trade_id == "T0001" and len(gw.state["created"]) == 1

    paid = []

    async def checker(tid):
        return await gw.gmpay.check_status(tid, client=gw.client)
    bot = Bot()
    assert run(gw.gmon.poll_gmpay(bot, checker)) == 0                              # 未付
    gw.state["status"]["T0001"] = 2
    assert run(gw.gmon.poll_gmpay(bot, checker)) == 1
    assert gw.commerce.get_order(o.id).state == "paid" and gw.commerce.get_order(o.id).paid_at
    assert bot.sent                                                                # 客户/管理群已通知
    assert run(gw.gmon.poll_gmpay(Bot(), checker)) == 0                            # 已付订单不再轮询


def test_switching_network_creates_second_gateway_order(gw):
    o = quote(gw)
    run(gw.commerce.attach_gateway(o.id, "tron", "usdt", client=gw.client))
    o2 = run(gw.commerce.attach_gateway(o.id, "bsc", "usdt", client=gw.client))
    assert o2.gm_trade_id == "T0002" and gw.state["created"][1]["order_id"] == f"{o.id}-1"


def test_callback_exact_duplicate_and_forged(gw):
    o = run(gw.commerce.attach_gateway(quote(gw).id, "tron", "usdt", client=gw.client))
    cb = {"pid": PID, "trade_id": o.gm_trade_id, "order_id": o.id, "amount": 100, "actual_amount": float(o.amount_str),
          "receive_address": o.pay_address, "token": "USDT", "block_transaction_id": "ab" * 32, "status": 2}
    cb["signature"] = gw.gmpay.sign(cb, SECRET)
    assert run(gw.gmon.handle_callback(Bot(), dict(cb, signature="0" * 64))) is False
    assert gw.commerce.get_order(o.id).state == "awaiting_payment"                 # 伪造回调不改变订单
    assert run(gw.gmon.handle_callback(Bot(), cb)) is True
    done = gw.commerce.get_order(o.id)
    assert done.state == "paid" and done.paid_txid == "ab" * 32
    assert run(gw.gmon.handle_callback(Bot(), cb)) is True                          # 重复回调幂等
    assert gw.commerce.settle_gmpay(o.gm_trade_id)[0] == "duplicate"


def test_underpaid_goes_to_review_and_small_gap_is_tolerated(gw):
    o = run(gw.commerce.attach_gateway(quote(gw).id, "tron", "usdt", client=gw.client))
    verdict, _ = gw.commerce.settle_gmpay(o.gm_trade_id, actual_amount="90")
    assert verdict == "underpaid" and gw.commerce.get_order(o.id).state == "payment_review"
    o2 = run(gw.commerce.attach_gateway(quote(gw, "200").id, "tron", "usdt", client=gw.client))
    verdict, _ = gw.commerce.settle_gmpay(o2.gm_trade_id, actual_amount="199.4")    # 差额 0.6 ≤ 容差
    assert verdict == "tolerance" and gw.commerce.get_order(o2.id).state == "paid"


def test_expired_late_goes_to_review(gw):
    o = run(gw.commerce.attach_gateway(quote(gw).id, "tron", "usdt", client=gw.client))
    with gw.db.SessionMaker() as s:
        s.get(gw.model.Order, o.id).expires_at = 1.0
        s.commit()
    assert gw.commerce.settle_gmpay(o.gm_trade_id)[0] == "expired_late"
    assert gw.commerce.get_order(o.id).state == "payment_review"


def test_unknown_trade_is_not_mine_and_gateway_down_is_harmless(gw):
    assert gw.commerce.settle_gmpay("NOPE") == ("not_mine", None)
    o = run(gw.commerce.attach_gateway(quote(gw).id, "tron", "usdt", client=gw.client))

    async def down(tid):
        raise gw.gmpay.GatewayError("down")
    assert run(gw.gmon.poll_gmpay(Bot(), down)) == 0
    assert gw.commerce.get_order(o.id).state == "awaiting_payment"


def test_unconfigured_gateway_is_inert(env, monkeypatch):
    monkeypatch.delenv("GMPAY_PID", raising=False)
    monkeypatch.delenv("GMPAY_SECRET", raising=False)
    gm = __import__("importlib").import_module("interactive-bot.commerce.providers.gmpay")
    mon = __import__("importlib").import_module("interactive-bot.commerce.gmpay_monitor")
    assert not gm.configured() and run(mon.poll_gmpay(Bot())) == 0
