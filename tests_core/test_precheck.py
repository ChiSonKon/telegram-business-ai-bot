"""预检优先:先弄清来意、收齐信息,再转 AI;预检阶段无"人工"字样、无人机验证、不调用模型。"""
import asyncio
from datetime import datetime, timedelta

from test_integration import Bot, LLM, turn


def admit(env, uid, text, bot=None):
    env.service.record_message(uid, "in", text)
    bot = bot or Bot()
    return asyncio.run(env.gate.admit(bot, uid, uid, text, channel="bot")), bot


def all_text(bot):
    return " ".join(m["text"] for m in bot.sent)


def test_vague_intent_asks_need_and_does_not_call_ai(env):
    g, bot = admit(env, 7, "我想了解静态IP")                         # WP1:先发说明卡,再进入问答
    assert not g.ai and env.service.in_precheck(7)
    assert "需要哪种 IP" in bot.sent[-1]["text"]
    llm = LLM()
    assert not asyncio.run(turn(env, Bot(), llm))            # 预检未完成:AI 回合直接拒绝
    assert llm.calls == 0


def test_answer_completes_precheck_then_first_ai_turn(env):
    env.precheck.begin(7, "prospect", "我想了解", line="other_biz")      # 直接测问答引擎;入口路由见 test_wp1_front
    g, bot = admit(env, 7, "想让新客户自动收到欢迎语并登记需求")
    assert g.ai and g.immediate and not env.service.in_precheck(7)
    assert env.service.get_role(7) == "prospect"
    assert "需求已整理" in bot.sent[0]["text"] and "其他业务需求" in bot.sent[0]["text"]
    assert g.texts == ["我想了解"]                                  # WP5:首轮交给 AI 的是客户真实首句,不再是伪装成原话的"预检摘要"
    assert asyncio.run(turn(env, Bot(), LLM(), text=g.texts[0]))    # 预检完成后 AI 才能工作


def test_rich_first_message_skips_known_steps(env):
    g, bot = admit(env, 7, "我想定制一个客服机器人，能自动回复客户")
    assert not g.ai and "最重要的一个功能" in bot.sent[-1]["text"]   # 业务与需求已知,不再问需求;说明卡在前
    assert any("跳过" in b.text for row in bot.sent[-1]["reply_markup"].inline_keyboard for b in row)
    g2, bot2 = admit(env, 7, "能对接我们现有的订单系统")
    assert not g2.ai and "希望什么时候用上" in bot2.sent[-1]["text"]   # 文字回答填入核心功能,继续下一题(最多 3 题)


def test_precheck_cards_never_say_manual_support_zh(env):
    seen = []
    for role, text in (("prospect", "我想了解机器人定制"), ("support", "我买了软件但激活不了"), ("partner", "想谈渠道代理合作")):
        env.precheck.begin(7, role, text)
        for _ in range(8):
            p = env.precheck.prompt(7, "zh")
            if not p:
                break
            seen.append(p[0] + " ".join(b.text for row in p[1].inline_keyboard for b in row) if p[1] else p[0])
            step = env.precheck.current_step(7)
            if p[1]:
                first = p[1].inline_keyboard[0][0].callback_data.split(":")
                env.precheck.answer(7, step, first[-1])
            else:
                env.precheck.accept_text(7, "一些内容说明说明", "zh")
        done = env.precheck.complete(7, "zh")
        if done:
            seen.append(done.text)
    assert seen and not [x for x in seen if "人工" in x]


def test_payment_issue_goes_to_team_not_ai(env):
    g, bot = admit(env, 7, "我已经付款了但是一直没到账，订单还在等")       # WP4:无订单 → 问一句(时间/金额/哈希),下一条即转交 P0
    assert not g.ai and not env.service.is_human_locked(7) and "付款的时间、金额" in bot.sent[0]["text"]
    g, bot = admit(env, 7, "昨天下午 3 点转的 288 USDT,没有哈希")
    assert not g.ai and env.service.is_human_locked(7)
    assert "已加急" in bot.sent[0]["text"] and "人工" not in bot.sent[0]["text"]
    with env.db.SessionMaker() as s:
        note = s.query(env.model.Outbox).one()
        assert "P0" in note.text and note.case_uid == 7


def test_partner_flow_collects_three_answers_then_hands_off(env):
    g, bot = admit(env, 7, "想谈渠道代理合作")
    assert "规模或资源" in bot.sent[0]["text"]               # 合作类型已从原话抽取,只问规模/资源
    g, bot = admit(env, 7, "我有一批企业客户资源")
    assert not g.ai and env.service.is_human_locked(7) and "合作意向已收到" in bot.sent[0]["text"]


def test_leave_message_asks_one_question_then_hands_off(env):
    g, bot = admit(env, 7, "我想留言")                       # D1:客户要真人 → 不绕弯,只问一句
    assert not g.ai and not env.service.is_human_locked(7) and "用一句话说说" in bot.sent[0]["text"]
    g, bot = admit(env, 7, "想咨询下上次定制的机器人加功能")
    assert env.service.is_human_locked(7) and "已交给同事" in bot.sent[0]["text"]
    env.precheck.begin(8, "prospect", "我想了解机器人定制")          # 预检进行中要求真人:同样一句话后转交,不再"先完成需求"
    g, bot = admit(env, 8, "转人工")
    assert not env.service.is_human_locked(8) and "用一句话说" in bot.sent[0]["text"]


def test_english_precheck_has_no_human_wording(env):
    env.runtime.lang_for(7, "hello")
    g, bot = admit(env, 7, "I need a license for the software")
    assert not g.ai and "first 4 characters" in bot.sent[0]["text"] and "human" not in all_text(bot).lower()     # WP4:license 问题 → 索要前 4 位,P0
    g2, bot2 = admit(env, 8, "hi, how much is it?")
    env.runtime.lang_for(8, "hi, how much is it?")
    assert not g2.ai and "human" not in all_text(bot2).lower()     # 信息不足:追问需求,不接 AI


def test_welcome_card_has_no_manual_wording_and_stale_button_ignored(env):
    g, bot = admit(env, 7, "你好呀")
    card = bot.sent[0]
    assert "人工" not in card["text"] and all("人工" not in b.text and "留言" not in b.text for row in card["reply_markup"].inline_keyboard for b in row)
    assert "欢迎来到白猫工作室" in card["text"]
    assert env.precheck.answer(7, "need", "x") is False        # 尚无预检时的旧卡片点击无效


def test_returning_customer_goes_through_precheck_again(env):
    env.service.set_intent(7, "x", role="support")
    with env.db.SessionMaker() as s:
        s.add(env.model.FrontdeskMessage(user_id=7, direction="in", text="old", created_at=datetime.utcnow() - timedelta(hours=30)))
        s.commit()
    g, bot = admit(env, 7, "你好")
    assert not g.ai and "欢迎回来" in bot.sent[0]["text"]            # 24h–7 天:回访卡(继续上次的 / 这次是新问题)
    assert {b.callback_data for row in bot.sent[0]["reply_markup"].inline_keyboard for b in row} == {"fd:resume:continue", "fd:resume:new"}


# ---------- 人机验证:只在频繁超额调用 AI 时出现 ----------
def test_normal_customer_never_challenged(env):
    admit(env, 7, "我想了解软件")
    assert env.abuse.needs_challenge(7) is False
    env.abuse.strike(7, "flood"); env.abuse.strike(7, "repeat")
    assert env.abuse.needs_challenge(7) is False               # 偶发 2 次不触发


def test_frequent_abuse_triggers_then_verified_clears(env):
    for _ in range(3):
        env.abuse.strike(7, "flood")
    assert env.abuse.needs_challenge(7) is True
    env.abuse.mark_verified(7)
    assert env.abuse.needs_challenge(7) is False


def test_ai_burst_triggers_challenge(env):
    with env.db.SessionMaker() as s:
        for i in range(8):
            s.add(env.model.AiUsageLedger(user_id=7, role="prospect", event="success", reply_units=1, reservation_key=f"b{i}"))
        s.commit()
    assert env.abuse.needs_challenge(7) is True


def test_challenge_can_be_globally_disabled(env):
    for _ in range(5):
        env.abuse.strike(7, "flood")
    env.gs.update_global_config({"disable_captcha": True})
    assert env.abuse.needs_challenge(7) is False


def test_leave_offer_removed_and_legacy_button_asks_one_question(env):
    import importlib
    from test_callbacks import FakeQuery
    cb = importlib.import_module("interactive-bot.handlers.callback_handlers")
    env.service.set_intent(7, "x", role="prospect")
    for _ in range(3):
        b = Bot()
        assert asyncio.run(turn(env, b, LLM()))
        assert not any("留言给同事" in str(m.get("reply_markup")) for m in b.sent)        # D1:不再有"聊满两轮才出现"的留言入口
    q = FakeQuery("frontdesk_leave:7"); q.bot = Bot()                                      # 旧卡片上的按钮仍可点:直接问一句话
    ctx = type("C", (), {"bot": q.bot, "user_data": {}, "bot_data": {}})()
    asyncio.run(cb.callback_frontdesk(type("U", (), {"callback_query": q})(), ctx))
    assert not env.service.is_human_locked(7) and "用一句话说说" in q.edits[-1][0]
    g, bot = admit(env, 7, "想改一下上次的方案")
    assert env.service.is_human_locked(7) and "已交给同事" in bot.sent[0]["text"]


def test_leave_offer_restored_when_d1_rejected(env, monkeypatch):
    import importlib
    config = importlib.import_module("interactive-bot.config")
    miss = config._MISSING
    monkeypatch.setattr(config, "_override", lambda k: 2 if k == "escalation.explicit_human.min_ai_turns" else miss)
    env.service.set_intent(7, "x", role="prospect")
    b1, b2 = Bot(), Bot()
    assert asyncio.run(turn(env, b1, LLM()))
    assert not any("留言给同事" in str(m.get("reply_markup")) for m in b1.sent)
    assert asyncio.run(turn(env, b2, LLM()))
    assert any("留言给同事" in str(m.get("reply_markup")) for m in b2.sent)                 # 业主否决 D1 时的旧行为


def test_legacy_human_intent_button_still_rejected(env):
    import importlib
    from test_callbacks import FakeQuery
    cb = importlib.import_module("interactive-bot.handlers.callback_handlers")
    q = FakeQuery("frontdesk_intent:human:7"); q.bot = Bot()                                # 旧欢迎卡上的"直接留言"
    ctx = type("C", (), {"bot": q.bot, "user_data": {}, "bot_data": {}})()
    asyncio.run(cb.callback_frontdesk(type("U", (), {"callback_query": q})(), ctx))
    assert not env.service.is_human_locked(7) and not q.edits


def test_real_lines_and_knowledge_retrieval(env):
    import importlib
    lines = importlib.import_module("interactive-bot.frontdesk.lines")
    retr = importlib.import_module("interactive-bot.rag.retriever")
    assert lines.match_line("我想买 WhiteCat 的授权")["id"] == "software"
    assert lines.match_line("想要 20 个住宅静态 IP")["id"] == "network"
    assert lines.match_line("ChatGPT Plus 代充")["id"] == "ai_account"
    assert lines.match_line("我要做个发卡商城机器人")["id"] == "bot_custom"
    rag = retr.search_knowledge("月度多少钱", line_id="software")
    assert "29U" in rag and "199U" in rag and "399U" in rag and "@oxbaimaobot" in rag and "空投" not in rag
    assert "0.43" in retr.search_knowledge("静态ip多少钱") and "288" in retr.search_knowledge("定制机器人多少钱")


def test_guardrail_accepts_real_prices_and_blocks_invented_ones(env):
    import importlib
    ai = importlib.import_module("interactive-bot.ai_turn")
    retr = importlib.import_module("interactive-bot.rag.retriever")
    from whitecat_core.guardrail import check_reply
    rag = retr.search_knowledge("月度套餐多少钱", line_id="software")
    auth = ai._authorizations(rag)
    assert check_reply("月度旗舰版 29 U / 30 天，购买入口 https://tg.web3baimao.com", **auth).ok
    assert check_reply("官方购买机器人 https://t.me/oxbaimaobot 可以购买", **auth).ok
    assert not check_reply("给您内部价 9 U", **auth).ok
