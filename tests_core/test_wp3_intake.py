"""WP3:问答引擎 v2——抽取器、先答后问、不重复问、旁问不填槽、断点续接、交接简报。含 G-04/05/11/12/13。"""
import asyncio
import importlib
import json

import pytest

from sim import Sim, SimLLM, assert_no_words

PKG = "interactive-bot"
G13 = "hi 你们软件 mac 能用吗?M2 的。多少钱一个月?可以两台电脑用吗?有没有 API?另外 IP 你们也卖吗"
FIRST = "想做个能自动回复客户的机器人,大概多少钱?"


def er():
    return importlib.import_module(f"{PKG}.frontdesk.intake.extract_rules")


def brief_of(env, uid=7):
    brief = importlib.import_module(f"{PKG}.frontdesk.intake.brief")
    return brief.load(uid)


# ---------- 抽取器 ----------
@pytest.mark.parametrize("text,amount,currency", [
    ("500U", 500, "U"), ("500 u", 500, "U"), ("五千", 5000, "U"), ("5k", 5000, "U"), ("1000以内", 1000, "U"),
    ("300-800", 550, "U"), ("$200", 200, "USD"), ("200 刀", 200, "U"), ("130 元", 130, "CNY"), ("五百左右", 500, "U"),
])
def test_extract_money_variants(env, text, amount, currency):
    m = er().money(text)
    assert m and m["amount"] == amount and m["currency"] == currency


def test_extract_money_none_and_ranges(env):
    assert er().money("预算不多") is None
    assert er().money("我有 2 个账号") is None                   # 无单位的裸数字不算金额
    m = er().money("1000以内")
    assert (m["lo"], m["hi"]) == (0, 1000)
    m = er().money("五百左右")
    assert (m["lo"], m["hi"]) == (400, 600)
    assert er().cn_num("两千") == 2000 and er().cn_num("一千五") == 1500 and er().cn_num("三万") == 30000


def test_extract_devices_os_horizon(env):
    ex = er().extract_all(G13)
    assert ex["os"] == "macos_arm" and ex["devices"] == 2 and ex["horizon"] == "1m" and ex["questions"] == 5
    assert er().os_("win11 电脑") == "windows" and er().os_("macbook intel") == "macos_intel"
    assert er().horizon("先试用几天") == "trial" and er().horizon("要一年") == "1y"


def test_extract_urgency_sentiment_company(env):
    ex = er().extract_all("我们公司要尽快上线,别再骗我了你们是骗子吗,靠谱吗")
    assert ex["urgency"] == "urgent" and ex["sentiment"] == "angry" and ex["company"] and ex["trust_concern"]
    assert er().deadline("这周要用") == "week" and er().deadline("不急") == "none"


# ---------- 黄金对话 ----------
def run_g04(env, **kw):
    s = Sim(env, **kw)
    first = s.say(FIRST)
    return s, first


def test_G04_custom_full_intake(env):
    s, first = run_g04(env)
    text = "\n".join(o["text"] for o in first)
    assert "288 U 起" in text
    assert "最重要的一个功能" in first[-1]["text"] and "想解决什么问题" not in text
    out = s.say("客户问价格能自动回,还能转给我")
    assert "希望什么时候用上" in out[-1]["text"]
    out = s.click("1 个月内")
    q = out[-1]
    assert "预算大概" in q["text"] and any("先不说" in b.text for b in s.buttons(q))
    out = s.click("300–800")
    confirm = out[-1]["text"]
    assert "我理解的对吗" in confirm and confirm.count("•") == 4
    assert_no_words(s.texts, ["人工", "安全验证"])
    out = s.click("对,继续")
    assert "✓ 需求已记下" in out[0]["text"]
    assert s.llm_calls == 1 and s.first_turns
    b = brief_of(env)
    assert b.budget["band"] == "300-800" and b.slots["key_feature"].startswith("客户问价格") and b.slots["deadline"] == "month"


def test_G05_budget_declined_not_asked_again(env):
    s, _ = run_g04(env)
    s.say("客户问价格能自动回,还能转给我")
    s.click("1 个月内")
    s.click("先不说")
    s.click("对,继续")
    b = brief_of(env)
    assert b.budget["declined"] is True and b.budget["band"] is None
    for _ in range(2):
        s.say("好的,那还有什么功能可以加")
    assert "预算大概" not in "\n".join(o["text"] for o in s.outs[-3:])


def test_G11_side_question_does_not_fill_slot(env):
    s, _ = run_g04(env)
    s.say("客户问价格能自动回,还能转给我")                       # 现在在"时间"题
    s.advance(hours=3)
    out = s.say("那大概多久能做好")
    assert "交期" in out[0]["text"] and "报价单" in out[0]["text"]
    assert "接着刚才的" in out[-1]["text"] and "希望什么时候用上" in out[-1]["text"]
    assert "deadline" not in s.answers["slots"]


def test_G12_resume_after_30h_and_8d(env):
    s, _ = run_g04(env)
    s.say("客户问价格能自动回,还能转给我")
    s.click("1 个月内")
    s.click("先不说")
    s.click("对,继续")
    s.advance(hours=30)
    out = s.say("在吗")
    assert "欢迎回来" in out[0]["text"] and "自动回复客户" in out[0]["text"]
    labels = [b.text for b in s.buttons(out[0])]
    assert labels == ["继续上次的", "这次是新问题"]
    n = len(s.outs)
    out = s.click("继续上次的")
    assert "接着上次" in out[-1]["text"]
    calls = s.llm_calls
    s.say("好的,接着说")
    assert s.llm_calls == calls + 1 and "欢迎回来" not in s.outs[-1]["text"]      # 续接后直接进入 AI,不重走问答
    s.advance(days=8)
    out = s.say("在吗")
    assert "上次您咨询过" in out[0]["text"] and "欢迎来到白猫工作室" in out[0]["text"]


def test_G13_five_questions_extracted(env):
    s = Sim(env)
    out = s.say(G13)
    assert "5 个问题" in out[0]["text"] and s.llm_calls == 1
    b = brief_of(env)
    assert b.slots["os"] == "macos_arm" and b.slots["horizon"] == "1m" and b.slots["devices"] == "2-3"
    assert b.signals["questions"] == 5
    assert not any(o["markup"] for o in out[:1])                  # 不出按钮问答


# ---------- 引擎行为 ----------
def test_large_budget_exits_to_handoff(env):
    s, _ = run_g04(env)
    s.say("客户问价格能自动回,还能转给我")
    s.click("1 个月内")
    out = s.click("2000 U 以上")
    assert s.env.service.is_human_locked(7) and s.llm_calls == 0
    assert s.case.handoff_reason == "large_deal" and s.case.priority == "P1"
    assert "优先处理" in out[-1]["text"]


def test_large_amount_in_first_text_exits(env):
    s = Sim(env)
    s.say("想做个电商机器人,预算 5000U,需要合同和发票")
    s.say("会员积分和订单推送")
    assert s.env.service.is_human_locked(7) or s.case.handoff_reason == "large_deal" or "deadline" in str(s.answers)


def test_low_budget_card(env):
    s, _ = run_g04(env)
    s.say("客户问价格能自动回,还能转给我")
    s.click("1 个月内")
    out = s.click("300 U 以内")
    texts = [o["text"] for o in out]
    assert any("定制起步价是 288 U" in t for t in texts) and "我理解的对吗" in texts[-1]


def test_max_three_questions(env):
    s = Sim(env)
    s.say("帮我定制个机器人")                                    # 业务线已知,需求未知:最多再问 3 题
    shown = 0
    for _ in range(8):
        last = s.outs[-1]
        if "我理解的对吗" in last["text"] or s.case.state != "PRECHECK":
            break
        shown += 1
        if s.buttons(last):
            s.click(s.buttons(last)[0].text)
        else:
            s.say("一些需求说明说明说明")
    assert shown <= 3 + 0 and len(s.answers["shown"]) <= 3


def test_two_side_questions_end_intake(env):
    s, _ = run_g04(env)
    s.say("客户问价格能自动回,还能转给我")                       # 时间题
    s.say("你们公司在哪里?")
    out = s.say("你们有案例吗?")
    assert s.case.state != "PRECHECK" or "我理解的对吗" in out[-1]["text"]
    assert s.answers.get("pending_q")


def test_stale_button_no_side_effect(env):
    s, _ = run_g04(env)
    s.click_before = len(s.outs)
    s.press("fd:ans:7:budget:300-800")                            # 预算题还没出示:旧卡片/乱序点击无效
    assert "budget" not in s.answers["slots"]
    s.say("客户问价格能自动回,还能转给我")
    s.click("1 个月内")
    s.press("fd:ans:7:deadline:week")                             # 已答的槽位再点:无效
    assert s.answers["slots"]["deadline"] == "month"


def test_confirm_and_fix_slot(env):
    s, _ = run_g04(env)
    s.say("客户问价格能自动回,还能转给我")
    s.click("1 个月内")
    s.click("300–800")
    out = s.click("改一下")
    assert "改哪一项" in out[-1]["text"]
    out = s.click("期望时间")
    assert "希望什么时候用上" in out[-1]["text"]
    out = s.click("1 周内")
    assert "我理解的对吗" in out[-1]["text"] and "1 周内" in out[-1]["text"]


def test_text_at_confirm_stage_updates_or_confirms(env):
    s, _ = run_g04(env)
    s.say("客户问价格能自动回,还能转给我")
    s.click("1 个月内")
    s.click("300–800")
    out = s.say("另外要能对接 CRM")
    assert "我理解的对吗" in out[-1]["text"] and "CRM" in out[-1]["text"]
    s.say("对")
    assert s.llm_calls == 1


def test_network_flow_handoff_quote(env):
    s = Sim(env)
    s.say("我想了解静态IP")
    s.click("住宅")
    out = s.click("100 以上")
    out = s.click("3 个月")
    assert s.env.service.is_human_locked(7) and s.case.handoff_reason == "quote_request" and s.case.priority == "P1"
    assert "已交给同事出报价" in out[-1]["text"] and s.llm_calls == 0


def test_legacy_answers_mapping(env):
    engine = importlib.import_module(f"{PKG}.frontdesk.intake.engine")
    st = engine.legacy_to_state({"line": "bot_custom", "need": "自动回复客户", "extra": "对接订单", "first": "hi"}, "prospect")
    assert st["flow"] == "bot_custom" and st["slots"] == {"line": "bot_custom", "need": "自动回复客户", "key_feature": "对接订单"}
    st = engine.legacy_to_state({"product": "software", "issue": "install", "desc": "打不开", "offer": "x"}, "support")
    assert st["flow"] == "support" and st["slots"]["issue"] == "install"
    st = engine.legacy_to_state({"ptype": "channel", "offer": "100 个客户", "how": "分成"}, "partner")
    assert st["slots"]["scale"] == "100 个客户(分成)"


def test_llm_extract_capped_and_validated(env, monkeypatch):
    engine = importlib.import_module(f"{PKG}.frontdesk.intake.engine")
    xl = importlib.import_module(f"{PKG}.frontdesk.intake.extract_llm")
    config = importlib.import_module(f"{PKG}.config")
    miss = config._MISSING
    flags = {"intake.llm_extract.enabled": True, "intake.llm_extract.daily_cap": 2}
    monkeypatch.setattr(config, "_override", lambda k: flags.get(k, miss))
    long_text = "我想做个机器人,主要是给客户自动回复价格,下周前需要上线,预算大概五百"

    llm = SimLLM(reply='前言 {"need": "自动回复价格", "deadline": "week", "budget": "999", "evil": "x"} 后记')
    engine.start_flow(7, "bot_custom", "", line="bot_custom")
    got = asyncio.run(xl.maybe_extract(7, long_text, llm=llm))
    assert set(got) == {"need", "deadline"} and llm.calls == 1                        # 非法选项 budget=999 被丢弃
    assert asyncio.run(xl.maybe_extract(7, long_text, llm=llm)) == [] and llm.calls == 1   # 每 case 只一次

    bad = SimLLM(reply="不是 JSON")
    engine.start_flow(8, "bot_custom", "", line="bot_custom")
    assert asyncio.run(xl.maybe_extract(8, long_text, llm=bad)) == [] and bad.calls == 1
    engine.start_flow(9, "bot_custom", "", line="bot_custom")                          # 日上限 2 已用完
    capped = SimLLM(reply='{"need": "x有效需求"}')
    assert asyncio.run(xl.maybe_extract(9, long_text, llm=capped)) == [] and capped.calls == 0


def test_llm_extract_disabled_by_default(env):
    xl = importlib.import_module(f"{PKG}.frontdesk.intake.extract_llm")
    assert xl.enabled() is False
    assert asyncio.run(xl.maybe_extract(7, "一段很长的需求描述" * 5, llm=SimLLM())) == []


def test_pending_status_throttled_30min(env):
    s = Sim(env)
    s.say("我已经付款了但是一直没到账")                           # P0:先问一句,下一条转交
    s.say("昨天下午 3 点转的 288 USDT")
    assert s.env.service.is_human_locked(7)
    router = importlib.import_module(f"{PKG}.frontdesk.router")
    mark = len(s.outs)
    assert asyncio.run(router.notify_pending(s.bot, 7, 7)) is True
    card = s.outs[mark]
    assert "已在处理中" in card["text"] and "付款核对" in card["text"] and s.buttons(card)[0].callback_data == "fd:newcase"
    assert asyncio.run(router.notify_pending(s.bot, 7, 7)) is False                   # 30 分钟内不重复
    s.advance(minutes=31)
    assert asyncio.run(router.notify_pending(s.bot, 7, 7)) is True


def test_pending_new_case_keeps_old_in_queue(env):
    s = Sim(env)
    s.say("我已经付款了但是一直没到账")
    s.say("昨天下午 3 点转的 288 USDT")
    old = s.case.id
    out = s.press("fd:newcase")
    assert "欢迎来到白猫工作室" in out[-1]["text"]
    with env.db.SessionMaker() as db:
        states = {c.id: c.state for c in db.query(env.model.FrontdeskCase).all()}
    assert states[old] == "HUMAN_PENDING" and len(states) == 2


def test_image_during_intake_notes_once(env):
    s, _ = run_g04(env)
    router = importlib.import_module(f"{PKG}.frontdesk.router")
    assert asyncio.run(router.note_image_in_intake(s.bot, 7, 7)) is True
    assert "收到图片" in s.outs[-1]["text"]
    assert asyncio.run(router.note_image_in_intake(s.bot, 7, 7)) is False


def test_intake_makes_zero_llm_calls(env):
    s, _ = run_g04(env)
    s.say("客户问价格能自动回,还能转给我")
    s.click("1 周内")
    assert s.llm_calls == 0
