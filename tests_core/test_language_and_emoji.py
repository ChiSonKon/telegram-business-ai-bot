# -*- coding: utf-8 -*-
import importlib
import asyncio
from types import SimpleNamespace
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import ContextTypes
import pytest

PKG = "interactive-bot"


def test_strip_leading_emoji_deduplication():
    from whitecat_core import runtime
    kit = importlib.import_module(f"{PKG}.ui.kit")
    
    # 启用 custom emoji
    b1 = kit.btn("🛒 买软件 / 看价格", icon="shop", style="primary")
    # 当有 icon 且 custom emoji 开启时，text 前缀的 🛒 被剥离，防止重叠
    if (b1.api_kwargs or {}).get("icon_custom_emoji_id"):
        assert "🛒" not in b1.text
        assert b1.text == "买软件 / 看价格"

    # 降级模式
    plain = kit.plain_markup(kit.kb([[b1]]))
    assert "买软件 / 看价格" in plain.inline_keyboard[0][0].text


def test_language_selection_flow(env):
    cmd = importlib.import_module(f"{PKG}.handlers.command_handlers")
    cb = importlib.import_module(f"{PKG}.handlers.callback_handlers")
    from sim import SimBot

    bot = SimBot()
    user_id = 99991
    user = SimpleNamespace(id=user_id, first_name="Tester", last_name=None, username="tester", full_name="Tester")
    
    # 模拟未锁定语言的用户进入 /start
    upd = SimpleNamespace(effective_user=user, effective_chat=SimpleNamespace(id=user_id), message=None)
    ctx = SimpleNamespace(bot=bot, user_data={}, bot_data={}, args=[])
    
    # 临时重置该用户语言状态
    env.runtime._langs.pop(user_id, None)
    
    # 执行 /start
    asyncio.run(cmd._start_customer(upd, ctx, user, "zh", user_id, ""))
    
    # 应当返回欢迎卡或语言卡
    assert len(bot.outs) > 0
    
    async def dummy(*a, **kw):
        pass

    # 测试 callback_set_lang 回调
    query_obj = SimpleNamespace(
        id="q1",
        from_user=user,
        data="set_lang:zh",
        answer=dummy,
        edit_message_text=dummy,
        message=SimpleNamespace(message_id=1, chat_id=user_id),
    )
    cb_upd = SimpleNamespace(callback_query=query_obj, effective_chat=SimpleNamespace(id=user_id))
    asyncio.run(cb.callback_set_lang(cb_upd, ctx))
    
    # 验证语言已锁定为 zh
    assert env.runtime.lang_for(user_id) == "zh"
    assert env.runtime.is_lang_locked(user_id) is True


def test_normalize_model_name():
    client_mod = importlib.import_module(f"{PKG}.llm.client")
    fn = client_mod.normalize_model_name
    ds = "https://api.deepseek.com/v1"
    assert fn(ds, "DeepSeek-V4.1-Flash") == "deepseek-flash"               # 官方定价页现行名
    assert fn(ds, "deepseek-flash") == "deepseek-flash"
    assert fn(ds, "deepseek-v4-flash") == "deepseek-flash"                  # 遗留名(仍可用)归一到现行名
    assert fn(ds, "DeepSeek-V4-Pro") == "deepseek-v4-pro"
    assert fn(ds, "deepseek-chat") == "deepseek-flash" and fn(ds, "deepseek-reasoner") == "deepseek-flash"
    assert fn("https://api.openai.com/v1", "gpt-4o-mini") == "gpt-4o-mini"
    assert fn("https://relay.example/v1", "Claude-Sonnet") == "Claude-Sonnet"


def test_deepseek_requests_disable_thinking_and_retry_without_on_rejection():
    import asyncio
    from types import SimpleNamespace
    client_mod = importlib.import_module(f"{PKG}.llm.client")
    c = client_mod.LLMClient("https://api.deepseek.com/v1", "k", "deepseek-flash")
    seen = []

    class Completions:
        async def create(self, **kw):
            seen.append(kw)
            if len(seen) == 1:
                raise RuntimeError("Error code: 400 - unknown field thinking")
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="好"))], usage=None)
    c.client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    assert asyncio.run(c.chat("sys", [{"role": "user", "content": "hi"}])) == "好"
    assert seen[0]["extra_body"] == {"thinking": {"type": "disabled"}} and "extra_body" not in seen[1]
    other = client_mod.LLMClient("https://api.openai.com/v1", "k", "gpt-4o-mini")
    seen.clear()
    other.client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    seen.append("skip-first")                                               # 让 Completions 直接成功
    asyncio.run(other.chat("sys", [{"role": "user", "content": "hi"}]))
    assert "extra_body" not in seen[-1]


def test_llm_retries_with_configured_name_when_normalized_is_rejected():
    import asyncio
    from types import SimpleNamespace
    client_mod = importlib.import_module(f"{PKG}.llm.client")
    c = client_mod.LLMClient("https://api.deepseek.com/v1", "k", "DeepSeek-Custom-Flash")
    used = []

    async def fake_create(model, messages):
        used.append(model)
        if model == "deepseek-flash":
            raise RuntimeError("Error code: 400 - The model `deepseek-flash` does not exist")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="好的"))], usage=None)
    c._create = fake_create
    assert asyncio.run(c.chat("sys", [{"role": "user", "content": "hi"}])) == "好的"
    assert used == ["deepseek-flash", "DeepSeek-Custom-Flash"]


def test_llm_empty_content_is_a_failure():
    import asyncio
    from types import SimpleNamespace
    client_mod = importlib.import_module(f"{PKG}.llm.client")
    c = client_mod.LLMClient("https://api.deepseek.com/v1", "k", "deepseek-flash")

    async def fake_create(model, messages):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))], usage=None)
    c._create = fake_create
    import pytest
    with pytest.raises(RuntimeError):
        asyncio.run(c.chat("sys", [{"role": "user", "content": "hi"}]))


def test_open_case_card_in_group_routes_to_dm(env):
    console = importlib.import_module(f"{PKG}.admin.console")
    
    admin_id = 1
    answers = []
    
    class FakeBot:
        def __init__(self):
            self.sent = []
        async def send_message(self, **kw):
            self.sent.append(kw)
            return SimpleNamespace(message_id=99)

    fake_bot = FakeBot()

    class GroupQuery:
        data = "adm:case:7"
        from_user = SimpleNamespace(id=admin_id)
        message = SimpleNamespace(chat=SimpleNamespace(id=-1001234567, type="supergroup"), chat_id=-1001234567)
        async def answer(self, text="", show_alert=False):
            answers.append((text, show_alert))
        def get_bot(self):
            return fake_bot

    ctx = SimpleNamespace(bot=fake_bot, user_data={})
    upd = SimpleNamespace(callback_query=GroupQuery())
    
    asyncio.run(console.callback_admin(upd, ctx))
    assert len(fake_bot.sent) == 1
    assert fake_bot.sent[0]["chat_id"] == admin_id
    assert any("私聊窗口" in a[0] for a in answers)



def test_env_default_ai_is_last_resort_when_panel_key_is_rejected(env, monkeypatch):
    import asyncio
    fb = importlib.import_module(f"{PKG}.llm.fallback_client")
    ai = importlib.import_module(f"{PKG}.ai_settings")
    panel = {"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "api_key": "sk-panel-key-bad",
             "model": "deepseek-flash", "enabled": True, "priority": 1}
    monkeypatch.setattr(fb, "get_enabled_ai_configs", lambda: [panel])
    monkeypatch.setattr(ai, "_default_ai_configs", lambda: [{"id": "default", "name": "默认AI", "base_url": "https://api.deepseek.com/v1",
                                                            "api_key": "sk-env-key-good", "model": "deepseek-chat", "enabled": True, "priority": 1}])
    monkeypatch.setattr(fb, "_default_ai_configs", ai._default_ai_configs)
    used = []

    class FakeClient:
        def __init__(self, base_url, api_key, model):
            self.key = api_key

        async def chat(self, **kw):
            used.append(self.key)
            if self.key == "sk-panel-key-bad":
                raise RuntimeError("Error code: 401 - Authentication Fails, Your api key: ****bad is invalid")
            return "好的"
    monkeypatch.setattr(fb, "LLMClient", FakeClient)
    c = fb.FallbackLLMClient()
    assert asyncio.run(c.chat(system_prompt="s", messages=[])) == "好的"
    assert used == ["sk-panel-key-bad", "sk-env-key-good"]
    with env.db.SessionMaker() as s:
        alerts = [o.text for o in s.query(env.model.Outbox).all()]
    assert len(alerts) == 1 and "首选 AI 配置调用失败" in alerts[0] and "401" in alerts[0]


def test_business_ip_term_does_not_flip_language_and_double_read_counts_once():
    from whitecat_core.lang import LangState
    st = LangState()
    assert st.update("在吗,有那些业务") == "zh"
    assert st.update("IP") == "zh"
    assert st.update("IP") == "zh"          # 入口与前台各读一次同一条消息
    assert st.update("USDT") == "zh"
    assert st.update("vpn ok") == "zh"
    # 真正改说英文(≥3 词、连续两条不同消息)才切换
    assert st.update("do you have static ips") == "zh"
    assert st.update("what is the price for it") == "en"
