"""WP1:诚实门面——先答后问、真伪核验、Business 礼仪、客户视角模式。含黄金对话 G-01/02/03/08/09/10/20/24。"""
import importlib
import json

import pytest

from sim import Sim, SimLLM, assert_buttons, assert_no_words

PKG = "interactive-bot"
FAKE_ADDR = "T" + "abcdefghijkmnpqrstuvwxyzABCDEFGHJ"          # 34 位,形似 TRON 地址
OFFICIAL_ADDR = "T" + "Q" * 33


def metric_events(env):
    """WP2 前以 router._emit 为准;这里确认路由没有调用模型。"""
    return None


@pytest.fixture()
def official(env, monkeypatch):
    cfgmod = importlib.import_module(f"{PKG}.config")
    miss = cfgmod._MISSING
    values = {"trust.official_addresses": [{"network": "TRC20", "address": OFFICIAL_ADDR, "purpose": "payment"}]}
    monkeypatch.setattr(cfgmod, "_override", lambda key: values.get(key, miss))
    return env


# ---------- 黄金对话 ----------
def test_G01_price_first_from_software_site(env):
    s = Sim(env)
    s.start("src_web_tg")
    outs = s.say("多少钱")
    text = "\n".join(o["text"] for o in outs)
    for price in ("6.9", "29", "69", "199", "399"):
        assert f"{price} U" in text
    assert "免费试用" in text
    assert_buttons(outs[0], ["官方购买入口"])
    assert_no_words(s.texts, ["您这次是来", "人工", "安全"])
    assert s.llm_calls == 0
    assert s.answers.get("source") == "web_tg"
    assert s.case.state == "ANSWERED"


def test_G02_price_overview_unknown_source(env):
    s = Sim(env)
    outs = s.say("价格")
    text = outs[0]["text"]
    assert "6.9 U 起" in text and "288 U 起" in text and "0.43 U/个起" in text and "23 U" in text
    labels = [b.text for b in s.buttons()]
    assert len(labels) == 5
    assert not any("KYC" in l or "认证" in l or "号池" in l for l in labels)
    assert s.llm_calls == 0


def test_G03_capability_question_goes_to_ai(env):
    s = Sim(env)
    outs = s.say("你们软件能群发吗?会不会封号?")
    assert "WhiteCat 5.0" in outs[0]["text"] and outs[0]["markup"] is None        # 一行轻确认,不是四选一
    assert s.llm_calls == 1
    assert s.answers.get("line") == "software"
    assert s.llm.last_messages[-1]["content"] == "你们软件能群发吗?会不会封号?"
    assert_no_words(s.texts, ["您这次是来"])


def test_G08_business_greeting_disclosure_no_menu(env):
    s = Sim(env, channel="business")
    outs = s.say("在吗")
    assert len(outs) == 1
    out = outs[0]
    assert "AI 助理" in out["text"] and "老板也会看到" in out["text"]
    assert out["markup"] is None and out["effect"] is None and out["biz"] == "biz-conn-1"
    outs2 = s.say("你好")                                        # 披露只发一次
    assert "AI 助理代为接待" not in outs2[0]["text"]
    assert "我是老板" not in s.texts


@pytest.mark.parametrize("channel", ["bot", "business"])
def test_G09_is_bot_rule_answer(env, channel):
    s = Sim(env, channel=channel)
    outs = s.say("你是机器人吗")
    assert "AI 助理" in "\n".join(o["text"] for o in outs)
    assert s.llm_calls == 0


def test_G10_impersonation_check(official):
    env = official
    s = Sim(env)
    outs = s.say(f"@oxbaimao_vip 让我转账到 {FAKE_ADDR} 是你们的人吗")
    text = "\n".join(o["text"] for o in outs)
    assert "不是白猫官方账号" in text and "不是白猫的收款地址" in text
    assert "@oxbaimao —— 软件咨询" in text and "@oxbaimaobot" in text
    assert any("疑似冒充" in t for t in s.outbox())
    assert s.llm_calls == 0
    s2 = Sim(env, uid=8)
    assert "是白猫官方账号" in s2.say("@oxbaimao 是官方吗")[0]["text"]
    s3 = Sim(env, uid=9)
    assert "白猫的收款地址" in s3.say(f"{OFFICIAL_ADDR} 是你们的地址吗")[0]["text"]
    s4 = Sim(env, uid=10)
    t4 = s4.say(f"@fake_pay_guy 我已经转了 100U 给他,是你们的人吗")[0]["text"]
    assert "举报" in t4 and "无法追回" in t4


def test_G20_sensitive_registration(env):
    s = Sim(env)
    outs = s.say("能帮我过币安 KYC 吗")
    assert "平台认证咨询" in outs[0]["text"] and "不会索要" in outs[0]["text"]
    assert not s.env.service.is_human_locked(7)
    outs2 = s.say("币安个人认证,身份在国内")
    assert "已登记" in outs2[0]["text"]
    assert s.env.service.is_human_locked(7)
    assert s.llm_calls == 0
    assert_no_words(s.texts, ["真人", "活体", "成功", "保证"])
    with env.db.SessionMaker() as db:
        row = db.query(env.model.Outbox).one()
        assert "P2" in row.text


def test_G24_english_price_card(env):
    s = Sim(env, lang="en")
    outs = s.say("how much is the software")
    text = outs[0]["text"]
    assert "License pricing" in text and "Starter pass 6.9 U" in text and "Founding Partner · Lifetime 399 U" in text
    assert not any("一" <= ch <= "鿿" for ch in text)
    assert_buttons(outs[0], ["Official checkout"])
    buy = s.click("Official checkout")
    assert "Official checkout" in buy[-1]["text"] and not any("一" <= ch <= "鿿" for ch in buy[-1]["text"])


# ---------- 入口分类器 ----------
ENTRY_TABLE = [
    ("@oxbaimao 是官方吗", "verify_request"), ("is @someone_x official?", "verify_request"),
    ("你们是不是官方", "verify_request"), (f"{FAKE_ADDR} 是你们的地址吗", "verify_request"),
    ("我付了但是没到账", "payment_issue"), ("I paid but it didn't arrive", "payment_issue"),
    ("能退款吗", "refund"), ("I want a refund", "refund"),
    ("我要找真人", "human_request"), ("I want to talk to a real person", "human_request"),
    ("你是机器人吗", "is_bot"), ("are you a bot?", "is_bot"), ("你是真人吗", "is_bot"),
    ("能帮我过币安KYC吗", "sensitive"), ("can you do kyc for okx", "sensitive"),
    ("装不上,windows 提示保护你的电脑", "support"), ("my license key doesn't work", "support"),
    ("想谈渠道代理合作", "partner"), ("I'm interested in being a reseller", "partner"),
    ("多少钱", "price_ask"), ("how much is it", "price_ask"), ("机器人定制报价", "price_ask"),
    ("我想买软件", "buy_now"), ("I want to buy the software", "buy_now"),
    ("你们软件能群发吗?会不会封号?", "line_question"), ("can your software run on two computers?", "line_question"),
    ("想做个能自动回复客户的机器人", "line_question"),
    ("我想了解软件", "line_mention"), ("静态IP", "line_mention"),
    ("你们是做什么的", "browse"), ("what do you do", "browse"), ("有哪些服务", "browse"),
    ("你好", "greeting"), ("hello", "greeting"), ("在吗", "greeting"),
    ("嗯", "unknown"), ("thanks", "unknown"), ("hire", "unknown"),
    ("做个客服机器人要多久?", "line_question"),
]


@pytest.mark.parametrize("text,expected", ENTRY_TABLE)
def test_entry_classifier_table(env, text, expected):
    entry = importlib.import_module(f"{PKG}.frontdesk.entry")
    assert entry.classify_entry(text).kind == expected


def test_entry_angry_needs_paid_order(env):
    entry = importlib.import_module(f"{PKG}.frontdesk.entry")
    assert entry.classify_entry("骗子", has_paid_order=True).kind == "angry"
    assert entry.classify_entry("骗子", has_paid_order=False).kind != "angry"


# ---------- 其他 ----------
def test_official_buy_card_only_official_urls(env):
    answers = importlib.import_module(f"{PKG}.frontdesk.answers")
    policy = importlib.import_module(f"{PKG}.frontdesk.service").POLICY
    allowed = policy["knowledge"]["allowed_domains"]
    for lang in ("zh", "en"):
        _, markup = answers.official_buy(lang)
        urls = [b.url for row in markup.inline_keyboard for b in row if b.url]
        assert urls
        for u in urls:
            assert any(d in u for d in allowed) or u.startswith("https://t.me/oxbaimaobot"), u


def test_business_voice_disabled(env):
    import asyncio
    from sim import SimBot
    sender = importlib.import_module(f"{PKG}.humanlike_sender")
    called = []

    async def fake_voice(**kw):
        called.append(kw)
        return None
    sender.send_humanlike_voice = fake_voice
    bot = SimBot()
    asyncio.run(sender.send_humanlike_messages(bot=bot, chat_id=7, text="你好呀", business_connection_id="b1", force_voice=True))
    assert not called and bot.outs
    bot2 = SimBot()
    asyncio.run(sender.send_humanlike_messages(bot=bot2, chat_id=7, text="你好呀", force_voice=True))
    assert called and "AI 语音" in bot2.outs[0]["text"]


def test_knowledge_quarantine_not_in_rag(env):
    retriever = importlib.import_module(f"{PKG}.rag.retriever")
    kb = json.load(open(retriever._KB_FILE, encoding="utf-8"))
    assert "scraped_products" not in kb and "scraped_services" not in kb and kb["quarantine"]
    out = retriever.search_knowledge("空投 收益")
    assert "2000u" not in out.lower() and "女巫" not in out


def test_asclient_mode_treats_admin_as_customer(env):
    admin = 1
    s = Sim(env, uid=admin)
    assert env.sandbox.is_client_view(admin) is False
    env.sandbox.set_client_view(admin, True)
    outs = s.start()
    assert "欢迎来到白猫工作室" in outs[0]["text"] and "控制面板" not in outs[0]["text"]
    outs = s.say("多少钱")
    assert "授权价格" in outs[0]["text"] or "服务与参考价" in outs[0]["text"]
    env.sandbox.set_client_view(admin, False)
    assert env.sandbox.is_client_view(admin) is False


def test_asclient_reset_clears_state(env):
    s = Sim(env)
    s.say("多少钱")
    assert s.case is not None
    env.sandbox.reset_client_state(7)
    assert s.case is None


def test_order_command_privacy(env):
    with env.db.SessionMaker() as db:
        db.add(env.model.Order(id="ABCDEF1234", user_id=7, sku="custom", title="定制机器人", state="awaiting_payment", stars=500))
        db.commit()
    mine = env.trust.order_view("abcdef1234", 7, "zh")
    assert "ABCDEF1234" in mine and "500" in mine
    other = env.trust.order_view("ABCDEF1234", 99, "zh")
    assert "500" not in other and "存在" in other
    admin = env.trust.order_view("ABCDEF1234", 1, "zh", is_admin=True)
    assert "500" in admin
    assert "没有找到订单" in env.trust.order_view("ZZZZ", 7, "zh")


def test_start_ord_param_shows_order(env):
    with env.db.SessionMaker() as db:
        db.add(env.model.Order(id="ABCDEF1234", user_id=7, sku="custom", title="定制机器人", state="awaiting_payment", stars=500))
        db.commit()
    s = Sim(env)
    outs = s.start("ord_ABCDEF1234")
    assert "ABCDEF1234" in outs[0]["text"]


def test_software_pick_flow(env):
    s = Sim(env)
    s.start("src_web_tg")
    s.say("多少钱")
    s.click("帮我选套餐")
    outs = s.click("1 个月")
    assert "月度旗舰版" in outs[-1]["text"] and "29 U" in outs[-1]["text"] and "17 U" in outs[-1]["text"]
    assert s.llm_calls == 0


def test_voice_message_gets_reply(env):
    # 语音由 handler 处理;此处验证文案存在且双语齐全
    config = importlib.import_module(f"{PKG}.config")
    assert "语音" in config.copy("voice.reply", "zh") and "voice" in config.copy("voice.reply", "en")


def test_copy_keys_complete(env):
    dc = importlib.import_module(f"{PKG}.config.defaults_copy")
    config = importlib.import_module(f"{PKG}.config")
    for key, val in dc.COPY.items():
        assert val.get("zh") and val.get("en"), key
    with pytest.raises(KeyError):
        config.copy("no.such.key", "zh")
    # 预检/问答阶段的客户文案不得出现"人工"、不实安全宣称
    for key in ("welcome", "card.price_software", "card.price_overview", "card.official_buy", "card.is_bot",
                "card.custom_intro", "card.network_intro", "card.ai_intro", "intake.side_noted"):
        for lang in ("zh", "en"):
            txt = dc.COPY[key][lang]
            assert "人工" not in txt and "安全验证" not in txt and "安全预检" not in txt
            assert "Security Verified" not in txt


def test_legacy_frontdesk_switches_are_ignored_after_wp11(env, monkeypatch):
    monkeypatch.setenv("FRONTDESK_V2", "0")
    monkeypatch.setenv("INTAKE_V2", "0")
    s = Sim(env)
    outs = s.say("我想了解软件")
    assert not env.service.in_precheck(7)
    assert outs
