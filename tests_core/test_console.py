"""管理员控制台:待办队列、处理动作、审计、引导式输入、提醒任务。"""
import asyncio
import importlib
from datetime import datetime, timedelta

import pytest

from test_integration import Bot, LLM, turn

ADMIN = 1


@pytest.fixture()
def adm(env):
    pkg = "interactive-bot"
    env.q = importlib.import_module(f"{pkg}.admin.queries")
    env.actions = importlib.import_module(f"{pkg}.admin.actions")
    env.views = importlib.import_module(f"{pkg}.admin.views")
    env.wiz = importlib.import_module(f"{pkg}.admin.wizards")
    env.console = importlib.import_module(f"{pkg}.admin.console")
    env.outbox = importlib.import_module(f"{pkg}.outbox")
    return env


def make_pending(env, uid=7, reason="support_escalation:payment", minutes=45, role="support"):
    env.service.set_intent(uid, "x", role=role)
    env.service.handoff(uid, reason, summary="[support] 产品:TG软件 问题:付款未到账")
    with env.db.SessionMaker() as s:
        c = s.query(env.model.FrontdeskCase).filter_by(user_id=uid).first()
        c.handoff_at = datetime.utcnow() - timedelta(minutes=minutes)
        s.commit()


def test_queue_sorted_by_priority_then_wait(adm):
    with adm.db.SessionMaker() as s:
        s.add(adm.model.User(user_id=8, first_name="B", chat_mode="AI_MODE")); s.commit()
    make_pending(adm, 7, "visitor_message", 300, "visitor")
    make_pending(adm, 8, "support_escalation:payment", 5)
    qs = adm.q.handoff_queue()
    assert [x["uid"] for x in qs] == [8, 7] and qs[0]["prio"] == "P0"          # 紧急优先于等得更久的留言
    assert [x["uid"] for x in adm.q.handoff_queue(urgent_only=True)] == [8]


def test_wp10_queue_filters_and_owner(adm):
    make_pending(adm, reason="quote_request")
    assert [x["uid"] for x in adm.q.handoff_queue(flt="quote")] == [7]
    assert adm.q.handoff_queue(flt="mine", admin_id=1) == []
    adm.actions.take_over(1, 7)
    assert [x["uid"] for x in adm.q.handoff_queue(flt="mine", admin_id=1)] == [7]
    assert adm.q.handoff_queue(flt="mine", admin_id=2) == []


def test_wp10_case_brief_and_recent_messages(adm):
    make_pending(adm)
    with adm.db.SessionMaker() as s:
        c = s.query(adm.model.FrontdeskCase).filter_by(user_id=7).first()
        c.brief_json = '{"source":"telegram", "budget":"300-500", "signals":["急"]}'
        for i in range(7):
            s.add(adm.model.FrontdeskMessage(user_id=7, case_id=c.id, direction="in" if i % 2 else "out", text=f"消息{i}"))
        s.commit()
    text, _ = adm.views.case(adm.q.case_view(7))
    assert "预算：300-500" in text and "信号：急" in text and "消息6" in text
    assert "消息0" not in text and "消息1" not in text


def test_wp10_order_export_projection(adm):
    with adm.db.SessionMaker() as s:
        s.add(adm.model.Order(id="WP10-1", user_id=7, sku="custom", title="服务", state="paid", amount_str="12.34", currency="USDT", network="TRC20", paid_txid="abc", created_at=datetime(2026, 10, 5)))
        s.commit()
    rows = adm.q.export_orders("2026-10")
    assert rows[0]["amount"] == "12.34" and rows[0]["txid"] == "abc"
    assert adm.q.export_orders("2026-09") == []
    with pytest.raises(ValueError):
        adm.q.export_orders("2026-13")


def test_home_shows_todo_first_and_is_renderable(adm):
    make_pending(adm)
    text, markup = adm.views.home()
    assert "需要你处理" in text and "紧急" in text and "AI 花费" in text
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any(l.startswith("待跟进") and "(1)" in l for l in labels) and "全部重置" not in " ".join(labels)


def test_case_card_content_and_buttons(adm):
    make_pending(adm)
    text, markup = adm.views.case(adm.q.case_view(7))
    assert "付款未到账" in text and "预检摘要" in text and "AI 额度" in text
    cbs = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
    assert {"adm:take:7", "adm:done:7", "adm:rel:7", "adm:grant:7:0", "adm:qt:7"} <= set(cbs)


def test_take_over_blocks_ai_and_is_audited(adm):
    adm.service.set_intent(7, "x", role="prospect")
    llm = LLM()
    assert adm.actions.take_over(ADMIN, 7) is True
    assert adm.service.case_state(7) == "HUMAN_ACTIVE" and not asyncio.run(turn(adm, Bot(), llm)) and llm.calls == 0
    assert adm.actions.take_over(ADMIN, 7) is False                      # 重复点击无副作用
    assert adm.q.audit_log()[0]["action"] == "接管"


def test_resolve_then_customer_is_retriaged_not_muted(adm):
    make_pending(adm, role="prospect", reason="explicit_human")
    assert adm.actions.resolve(ADMIN, 7)
    assert adm.service.case_state(7) == "RESOLVED" and not adm.service.is_human_locked(7)
    bot = Bot()
    adm.service.record_message(7, "in", "你好")
    g = asyncio.run(adm.gate.admit(bot, 7, 7, "你好", channel="bot"))
    assert not g.ai and "欢迎回来" in bot.sent[0]["text"]               # 重新预检,而不是被静默


def test_release_requires_state_and_role(adm):
    make_pending(adm, role="prospect", reason="explicit_human")
    assert adm.actions.release_to_ai(ADMIN, 7, "need") == "AI_SALES"
    assert adm.actions.release_to_ai(ADMIN, 7, "need") is None           # 已不在人工状态
    make_pending(adm, 8, "partner_intake", 5, "partner")
    assert adm.actions.release_to_ai(ADMIN, 8, "need") is None           # 合作类不恢复 AI
    assert "客户补充了新的业务需求" in adm.q.audit_log()[0]["detail"] or adm.q.audit_log()[0]["action"] == "恢复AI"


def test_grant_adds_rounds_and_release_works_after_quota_exhaustion(adm):
    adm.service.set_intent(7, "x", role="prospect")                       # WP5:support 剧本 2 轮后会主动交给同事,额度测试改用销售角色
    limit = adm.service.ROLE_LIMITS["prospect"]
    for _ in range(limit):
        assert asyncio.run(turn(adm, Bot(), LLM()))
    assert not asyncio.run(turn(adm, Bot(), LLM()))                      # 额度用完 → 转交
    assert adm.service.is_human_locked(7)
    assert adm.actions.grant_replies(ADMIN, 7, 6) == 6
    assert adm.actions.release_to_ai(ADMIN, 7, "quota") == "AI_SALES"
    llm = LLM()
    assert asyncio.run(turn(adm, Bot(), llm)) and llm.calls == 1         # 增加后真的可以继续


def test_legacy_topic_buttons_sync_frontdesk_state(adm):
    """话题里的旧按钮曾把 admin.id 当 owner_id 传入,导致前台状态不同步;现在统一走 actions。"""
    adm.service.set_intent(7, "x", role="prospect")
    adm.actions.take_over(ADMIN, 7)
    assert adm.service.case_state(7) == "HUMAN_ACTIVE"
    assert adm.actions.release_to_ai(ADMIN, 7, "话题按钮") == "AI_SALES"
    assert not adm.service.is_human_locked(7)


def test_find_user_by_id_and_username(adm):
    with adm.db.SessionMaker() as s:
        u = s.query(adm.model.User).filter_by(user_id=7).one(); u.username = "Alice"; s.commit()
    assert adm.q.find_user("7") == 7 and adm.q.find_user("@alice") == 7 and adm.q.find_user("@nobody") is None


# ---------- 引导式输入 ----------
def test_wizard_cost():
    from importlib import import_module
    w = import_module("interactive-bot.admin.wizards")
    assert w.parse_cost("0.27 1.1 5") == {"llm_price_in_per_m": 0.27, "llm_price_out_per_m": 1.1, "global_daily_budget_usd": 5.0}
    for bad in ("abc", "1", "1 2 0", "-1 2"):
        with pytest.raises(w.WizardError):
            w.parse_cost(bad)


def test_wizard_sku_line_ton():
    from importlib import import_module
    w = import_module("interactive-bot.admin.wizards")
    assert w.parse_sku("monthly 月度套餐 Monthly Plan 1500 6.5") == ("monthly", {"title_zh": "月度套餐", "title_en": "Monthly Plan", "stars": 1500, "ton": 6.5})
    assert w.parse_sku("trial 试用 Trial 50 -")[1].get("ton") is None
    for bad in ("Bad 名 N 1 1", "x 名 N - -", "x 名 N 1.5 1", "x 名"):
        with pytest.raises(w.WizardError):
            w.parse_sku(bad)
    line = w.parse_line("nft|NFT 铸造|NFT minting|nft,铸造|目标链和数量")
    assert line["keywords"] == ["nft", "铸造"] and line["extra"] is True
    with pytest.raises(w.WizardError):
        w.parse_line("nft|x|y|")
    assert w.parse_ton("UQ" + "a" * 46 + " key123") == {"address": "UQ" + "a" * 46, "api_key": "key123"}
    with pytest.raises(w.WizardError):
        w.parse_ton("not-an-address")


def test_confirm_flow_applies_only_after_ok(adm):
    class Q:
        data = "adm:ok"
        def __init__(self): self.edits = []
        from_user = type("U", (), {"id": ADMIN})()
        async def answer(self, *a, **k): pass
        async def edit_message_text(self, **kw): self.edits.append(kw)
        message = type("M", (), {"chat_id": 1})()

    class Ctx:
        user_data = {adm.console.PENDING_KEY: {"kind": "cost", "payload": {"llm_price_in_per_m": 0.5, "llm_price_out_per_m": 1.5, "global_daily_budget_usd": 3.0}}}
        bot = Bot()

    class Upd:
        callback_query = Q()

    assert adm.budget.prices() is None
    asyncio.run(adm.console.callback_admin(Upd(), Ctx()))
    assert adm.budget.prices() == (0.5, 1.5) and adm.budget.daily_budget() == 3.0
    assert adm.q.audit_log()[0]["action"] == "设置AI成本"
    asyncio.run(adm.console.callback_admin(Upd(), Ctx()))                # 再点一次:没有待确认项,不重复生效
    assert len(adm.q.audit_log()) == 1


def test_non_admin_cannot_use_console(adm):
    class Q:
        data = "adm:take:7"
        from_user = type("U", (), {"id": 999})()
        def __init__(self): self.answered = None
        async def answer(self, text="", show_alert=False): self.answered = text
    q = Q()
    upd = type("U", (), {"callback_query": q})()
    asyncio.run(adm.console.callback_admin(upd, type("C", (), {"user_data": {}, "bot": Bot()})()))
    assert q.answered == "No permission" and adm.service.case_state(7) is None


# ---------- 提醒 ----------
def test_sla_reminder_once_per_event_and_takeover_stale(adm):
    make_pending(adm, minutes=15)                                        # P0 阈值 10 分钟,已超时
    assert asyncio.run(adm.console.sla_check(Bot())) == 2                # 管理群提醒 1 条 + P0 超时私信业主 1 条(WP4)
    assert asyncio.run(adm.console.sla_check(Bot())) == 0                # 同一事件不重复提醒
    with adm.db.SessionMaker() as s:
        s.add(adm.model.User(user_id=9, first_name="C", chat_mode="AI_MODE")); s.commit()
    adm.service.set_intent(9, "x", role="prospect"); adm.actions.take_over(ADMIN, 9)
    with adm.db.SessionMaker() as s:
        c = s.query(adm.model.FrontdeskCase).filter_by(user_id=9).one(); c.updated_at = datetime.utcnow() - timedelta(hours=13); s.commit()
    assert asyncio.run(adm.console.sla_check(Bot())) == 1


def test_daily_digest_once_per_day(adm):
    assert asyncio.run(adm.console.daily_digest(Bot())) is True
    assert asyncio.run(adm.console.daily_digest(Bot())) is False


def test_migration_adds_columns_idempotently(adm):
    migrate = importlib.import_module("db.migrate")
    assert migrate.ensure_columns(adm.db.engine) == []                    # create_all 已建出新列,再次执行无副作用
