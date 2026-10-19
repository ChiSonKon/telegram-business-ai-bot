"""成交闭环:目录→报价→Stars/TON 结算→幂等→异常分类→凭证不等于付款。真实 SQLite + 假 Bot。"""
import asyncio

import pytest
from whitecat_core.payments import ton

from test_integration import Bot, LLM, Mem, setup_role, turn


ADDR = "UQ" + "b" * 46


@pytest.fixture()
def shop(env):
    env.commerce.set_catalog({"monthly": {"title_zh": "月度套餐", "title_en": "Monthly", "stars": 1500, "ton": 6.5}})
    env.commerce.set_ton_config({"address": ADDR})
    return env


def test_catalog_validation(env):
    with pytest.raises(ValueError):
        env.commerce.set_catalog({"x": {"title_zh": "x"}})
    with pytest.raises(ValueError):
        env.commerce.set_catalog({"x": {"stars": 1.5}})


def test_stars_exact_pays_and_replay_is_duplicate(shop):
    o = shop.commerce.create_quote(7, "monthly", "zh")
    assert shop.commerce.settle_stars(o.id, "chg1", 1500, "XTR") == "exact"
    assert shop.commerce.get_order(o.id).state == "paid"
    assert shop.commerce.settle_stars(o.id, "chg1", 1500, "XTR") == "duplicate"


def test_stars_wrong_amount_goes_to_review(shop):
    o = shop.commerce.create_quote(7, "monthly")
    assert shop.commerce.settle_stars(o.id, "c2", 100, "XTR") == "underpaid"
    assert shop.commerce.get_order(o.id).state == "payment_review"


def test_requote_supersedes_old_and_orphan_payment_is_flagged(shop):
    old = shop.commerce.create_quote(7, "monthly")
    shop.commerce.create_quote(7, "monthly")
    assert shop.commerce.get_order(old.id).state == "superseded"
    assert shop.commerce.settle_stars(old.id, "c3", 1500, "XTR") == "orphan_payment"   # 钱到了但订单已作废 → 人工处理


def _event(order, **kw):
    base = dict(network="ton", tx_hash="a" * 64, lt=10, to_address=ADDR, amount_nano=order.ton_nano,
                asset="TON", comment=order.ton_memo, ts=order.expires_at - 600)
    base.update(kw)
    return ton.ChainEvent(**base)


def test_ton_exact_then_replay(shop):
    o = shop.commerce.create_quote(7, "monthly")
    assert shop.commerce.settle_ton(_event(o)) == ("exact", o.id)
    assert shop.commerce.get_order(o.id).state == "paid"
    assert shop.commerce.settle_ton(_event(o))[0] == "duplicate"


@pytest.mark.parametrize("kw,verdict", [
    (dict(amount_nano=1), "underpaid"),
    (dict(asset="USDT"), "wrong_asset"),
])
def test_ton_anomalies_never_pay(shop, kw, verdict):
    o = shop.commerce.create_quote(7, "monthly")
    assert shop.commerce.settle_ton(_event(o, **kw))[0] == verdict
    assert shop.commerce.get_order(o.id).state == "payment_review"


def test_ton_late_payment_needs_human(shop):
    o = shop.commerce.create_quote(7, "monthly")
    v, _ = shop.commerce.settle_ton(_event(o, ts=o.expires_at + 60))
    assert v == "expired_late" and shop.commerce.get_order(o.id).state == "payment_review"


def test_forged_memo_is_not_mine(shop):
    o = shop.commerce.create_quote(7, "monthly")
    assert shop.commerce.settle_ton(_event(o, comment="WC-" + o.id + "-000000"))[0] == "not_mine"


def test_receipt_screenshot_never_pays(shop):
    o = shop.commerce.create_quote(7, "monthly")
    shop.commerce.transition(o.id, "awaiting_payment", actor="customer")
    bot = Bot()
    assert asyncio.run(shop.notify.register_receipt(bot, 7)) is True
    assert shop.commerce.get_order(o.id).state == "payment_review"
    assert len(bot.sent) == 2                       # 给客户的回执 + 给管理员的核对卡
    assert asyncio.run(shop.notify.register_receipt(Bot(), 999)) is False
    with pytest.raises(Exception):
        shop.commerce.transition(o.id, "paid", actor="x")      # 无已验证事实不可 paid


def test_poll_ton_with_fake_chain(shop):
    o = shop.commerce.create_quote(7, "monthly")
    payload = {"transactions": [
        {"hash": "h1", "lt": "5", "now": int(o.expires_at - 300),
         "in_msg": {"source": "EQsender", "value": str(o.ton_nano), "message_content": {"decoded": {"type": "text_comment", "comment": o.ton_memo}}}},
        {"hash": "h2", "lt": "6", "now": 1, "in_msg": {"source": None, "value": "5", "message_content": {}}},
    ]}

    async def fetcher(cfg):
        return payload

    bot = Bot()
    assert asyncio.run(shop.monitor.poll_ton(bot, fetcher)) == 1
    assert shop.commerce.get_order(o.id).state == "paid"
    assert asyncio.run(shop.monitor.poll_ton(Bot(), fetcher)) == 0       # 下一轮轮询到同一笔:幂等
    assert any("订单" in m["text"] for m in bot.sent)


def test_concurrent_transition_has_single_winner(shop):
    o = shop.commerce.create_quote(7, "monthly")
    shop.commerce.transition(o.id, "awaiting_payment", actor="c")
    results = [shop.commerce.transition(o.id, "payment_review", actor="a") for _ in range(3)]
    assert results.count(True) == 1


def test_global_budget_stops_model_calls(env):
    setup_role(env)
    env.gs.update_global_config({"llm_price_in_per_m": 1.0, "llm_price_out_per_m": 2.0, "global_daily_budget_usd": 0.001})
    with env.db.SessionMaker() as s:
        s.add(env.model.AiUsageLedger(user_id=99, role="prospect", event="success", reply_units=1, cost_usd=0.01, reservation_key="k-budget"))
        s.commit()
    llm = LLM()
    assert not asyncio.run(turn(env, Bot(), llm))
    assert llm.calls == 0 and env.service.is_human_locked(7)


def test_unpriced_uses_reply_cap_backstop(env):
    assert env.budget.exhausted() is None
    env.gs.update_global_config({"global_daily_reply_cap": 1})
    with env.db.SessionMaker() as s:
        s.add(env.model.AiUsageLedger(user_id=99, role="prospect", event="success", reply_units=1, reservation_key="k-cap"))
        s.commit()
    assert env.budget.exhausted() == "reply_cap"


def test_cost_is_recorded_when_priced(env):
    setup_role(env)
    env.gs.update_global_config({"llm_price_in_per_m": 1000.0, "llm_price_out_per_m": 2000.0})
    assert asyncio.run(turn(env, Bot(), LLM()))
    with env.db.SessionMaker() as s:
        assert s.query(env.model.AiUsageLedger).filter_by(event="success").one().cost_usd > 0


