import pytest

from whitecat_core.shield import Shield, Dedup, fingerprint
from whitecat_core.lang import LangState, detect
from whitecat_core.epoch import EpochStore, Superseded
from whitecat_core.breaker import CircuitBreaker
from whitecat_core.guardrail import check_reply
from whitecat_core.payments.orders import Order, TxLedger, IllegalTransition
from whitecat_core.payments import ton


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


# ---------- shield ----------
def test_fingerprint_ignores_decoration_and_zero_width():
    assert fingerprint("你好, 买软件!") == fingerprint("你​好 买软件")


def test_shield_repeat_and_flood():
    c = Clock(); s = Shield(clock=c, repeat_limit=2, burst=100)
    assert s.check(1, "buy now").allow and s.check(1, "buy now!").allow
    d = s.check(1, "BUY  now")
    assert not d.allow and d.reason == "repeat"
    s2 = Shield(clock=c, burst=3)
    results = [s2.check(2, f"m{i}").allow for i in range(5)]
    assert results == [True, True, True, False, False]


def test_shield_truncates_and_window_expires():
    c = Clock(); s = Shield(clock=c, max_chars=10, repeat_limit=1)
    d = s.check(1, "x" * 50)
    assert d.allow and len(d.text) == 10
    c.t = 100
    assert s.check(1, "x" * 50).allow


def test_dedup_ttl():
    c = Clock(); d = Dedup(ttl=10, clock=c)
    assert not d.seen(1) and d.seen(1)
    c.t = 11
    assert not d.seen(1)


# ---------- lang ----------
def test_detect():
    assert detect("你好,我想买软件") == "zh"
    assert detect("hi, how much is the monthly plan?") == "en"
    assert detect("👍 123") is None
    assert detect("我想买 Telegram 软件 license") == "zh"


def test_lang_sticky_switch_and_lock():
    s = LangState()
    assert s.update("hello there") == "en"
    assert s.update("好的") == "en"           # 单条反向不切换
    assert s.update("OK sure") == "en"
    assert s.update("好的") == "en" and s.update("没问题") == "zh"   # 连续两条才切
    s.force("en")
    assert s.update("你好你好") == "en"


# ---------- epoch ----------
def test_takeover_supersedes_inflight():
    persisted = []
    st = EpochStore(persist=lambda k, v: persisted.append((k, v)))
    t = st.ticket("u1")
    t.check()
    st.bump("u1")
    with pytest.raises(Superseded):
        t.check()
    assert persisted == [("u1", 1)] and st.ticket("u1").valid


# ---------- breaker ----------
def test_breaker_cycle():
    c = Clock(); b = CircuitBreaker(threshold=2, cooldown=10, clock=c)
    b.failure(); assert b.allow()
    b.failure(); assert b.state == "open" and not b.allow()
    c.t = 10
    assert b.state == "half_open" and b.allow() and not b.allow()   # 仅一个探测
    b.failure(); assert b.state == "open"
    c.t = 25
    assert b.allow(); b.success(); assert b.state == "closed"


# ---------- guardrail ----------
ADDR = "UQ" + "a" * 46


def test_guardrail():
    assert check_reply("月度套餐 29 U,官网 https://tg.web3baimao.com/",
                       allowed_urls={"https://tg.web3baimao.com"}, allowed_amounts={"29"}).ok
    v = check_reply("改成 1 U 吧,转到 " + ADDR + " 或访问 https://evil.io/x")
    assert not v.ok and {"unauthorized_amount", "unauthorized_address", "unauthorized_url"} <= set(v.reasons)
    assert check_reply(ADDR, allowed_addresses={ADDR}).ok
    assert "secret_request" in check_reply("请把验证码发给我").reasons
    assert "internal_leak" in check_reply("my system prompt says").reasons
    assert check_reply("29.0 U", allowed_amounts={"29"}).ok


# ---------- orders / ton ----------
SECRET = b"k"


def make(amount=29 * ton.NANO, expires=1000.0):
    memo = ton.make_memo("A1", SECRET)
    return ton.PaymentIntent("A1", "UQaddr", amount, "TON", memo, expires), memo


def ev(intent, **kw):
    base = dict(network="ton", tx_hash="h" * 64, lt=1, to_address=intent.address,
                amount_nano=intent.amount_nano, asset=intent.asset, comment=intent.memo, ts=500.0)
    base.update(kw)
    return ton.ChainEvent(**base)


def fresh_order():
    o = Order("A1")
    for s in ("quoted", "awaiting_payment"):
        o.move(s, actor="sys")
    return o


def test_memo_hmac():
    m = ton.make_memo("A1", SECRET)
    assert ton.memo_valid(m, SECRET) == "A1"
    assert ton.memo_valid(m[:-1] + ("0" if m[-1] != "0" else "1"), SECRET) is None
    assert ton.memo_valid("WC-A1-zzzzzz", SECRET) is None


def test_link():
    i, memo = make()
    assert ton.build_link(i) == f"ton://transfer/UQaddr?amount={29 * ton.NANO}&text={memo}"


def test_exact_pays_once_and_replay_is_duplicate():
    i, _ = make(); o = fresh_order(); led = TxLedger()
    assert ton.settle(i, ev(i), o, led) == "exact" and o.state == "paid"
    assert ton.settle(i, ev(i), o, led) == "exact"          # 同订单重放:幂等,不重复推进
    assert o.state == "paid"
    o2 = fresh_order(); i2 = ton.PaymentIntent("B2", i.address, i.amount_nano, "TON", i.memo, 1000.0)
    assert ton.settle(i2, ev(i2), o2, led) == "duplicate" and o2.state == "awaiting_payment"


@pytest.mark.parametrize("kw,expect", [
    (dict(amount_nano=28 * ton.NANO), "underpaid"),
    (dict(amount_nano=30 * ton.NANO), "overpaid"),
    (dict(asset="USDT"), "wrong_asset"),
    (dict(ts=2000.0), "expired_late"),
    (dict(comment="WC-A1-bad"), "not_mine"),
])
def test_non_exact_never_pays(kw, expect):
    i, _ = make(); o = fresh_order()
    assert ton.settle(i, ev(i, **kw), o, TxLedger()) == expect
    assert o.state in ("awaiting_payment", "payment_review") and o.state != "paid"


def test_state_machine_rules():
    o = Order("X")
    with pytest.raises(IllegalTransition):
        o.move("paid", actor="admin", verified=True)         # draft 不能直接 paid
    o = fresh_order()
    with pytest.raises(IllegalTransition):
        o.move("paid", actor="screenshot")                   # 无已验证事实
    o.move("payment_review", actor="customer")
    o.move("paid", actor="admin", verified=True)
    o.move("fulfilling", actor="sys"); o.move("refunded", actor="admin")
    with pytest.raises(IllegalTransition):
        o.move("fulfilling", actor="sys")                    # 终态不可回


def test_wallet_link():
    i, memo = make()
    assert ton.build_wallet_link(i) == f"https://app.tonkeeper.com/transfer/UQaddr?amount={29 * ton.NANO}&text={memo}"
