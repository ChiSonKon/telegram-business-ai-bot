"""WP0:基线清理。按钮不代表真人、待跟进不被重置、预检无不实安全宣称、敏感线不进 AI、代理显式开关。"""
import asyncio
import importlib

from test_callbacks import click
from test_integration import Bot

PKG = "interactive-bot"


def _case(env, uid=7):
    with env.db.SessionMaker() as s:
        return s.query(env.model.FrontdeskCase).filter_by(user_id=uid).order_by(env.model.FrontdeskCase.id.desc()).first()


def _pending(env, uid=7):
    env.service.handoff(uid, "explicit_human")
    assert _case(env, uid).state == "HUMAN_PENDING"


def test_button_click_does_not_clear_abuse_strikes(env):
    for _ in range(3):
        env.abuse.strike(7, "spam")
    assert env.abuse.needs_challenge(7)
    click(env, "frontdesk_intent:prospect:7")
    assert env.abuse.needs_challenge(7)


def test_start_keeps_pending_case(env):
    _pending(env)
    cmd = importlib.import_module(f"{PKG}.handlers.command_handlers")
    bot = Bot()
    user = type("U", (), {"id": 7, "first_name": "a", "last_name": None, "username": None})()
    upd = type("Upd", (), {"effective_user": user, "effective_chat": type("C", (), {"id": 7})(), "message": None})()
    ctx = type("C", (), {"bot": bot, "user_data": {}, "bot_data": {}})()
    asyncio.run(cmd.start(upd, ctx))
    assert _case(env).state == "HUMAN_PENDING"
    assert bot.sent and "处理中" in bot.sent[-1]["text"]


def test_intent_click_keeps_pending_case(env):
    _pending(env)
    q, _ = click(env, "frontdesk_intent:prospect:7")
    assert _case(env).state == "HUMAN_PENDING"
    assert not q.edits and "处理中" in q.answers[-1]


def test_precheck_copy_has_no_false_security_claims(env):
    texts = importlib.import_module(f"{PKG}.frontdesk.texts")
    banned = ("安全验证", "安全预检", "Security", "Verified")
    strings = []

    def walk(o):
        if isinstance(o, str):
            strings.append(o)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
    walk(env.precheck.P)
    walk(texts.T)
    for s in strings:
        assert not any(b in s for b in banned), s
    for lang in ("zh", "en"):
        for k in ("hold", "pick_first"):
            assert "人工" not in texts.T[lang][k]


def test_sensitive_lines_not_in_buttons_and_hand_off(env):
    env.precheck.begin(7, "prospect")
    text, markup = env.precheck.prompt(7, "zh")
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert not any(d.endswith(":kyc") or d.endswith(":account") for d in data)
    env.precheck.begin(7, "prospect", "能帮我过币安KYC吗")
    done = env.precheck.complete(7, "zh")
    assert done and done.kind == "handoff" and done.reason == "sensitive_line"
    assert "人工" not in done.text


def test_sensitive_notes_not_in_ai_brief(env):
    lines = importlib.import_module(f"{PKG}.frontdesk.lines")
    kyc = next(x for x in lines.get_lines() if x["id"] == "kyc")
    out = lines.brief(kyc, "zh")
    assert "无感认证" not in out and "无限补号" not in lines.brief(next(x for x in lines.get_lines() if x["id"] == "account"), "zh")


def test_proxy_explicit_only(monkeypatch):
    net = importlib.import_module(f"{PKG}.net")
    monkeypatch.delenv("TELEGRAM_PROXY", raising=False)
    monkeypatch.setattr(net, "_probe", lambda: (_ for _ in ()).throw(AssertionError("must not probe")))
    assert net.resolve_telegram_proxy() == (None, "none")
    monkeypatch.setenv("TELEGRAM_PROXY", "socks5://x")
    assert net.resolve_telegram_proxy() == ("socks5://x", "env")
    monkeypatch.setattr(net, "_probe", lambda: "http://127.0.0.1:7890")
    monkeypatch.setenv("TELEGRAM_PROXY", "auto")
    assert net.resolve_telegram_proxy() == ("http://127.0.0.1:7890", "auto")


def test_ack_task_is_retained(env):
    cb = importlib.import_module(f"{PKG}.handlers.callback_handlers")

    class Q:
        async def answer(self, *a, **k):
            pass

    async def run():
        cb._ack(Q())
        assert cb._ack_tasks
        await asyncio.gather(*list(cb._ack_tasks))
        await asyncio.sleep(0)
        assert not cb._ack_tasks
    asyncio.run(run())
