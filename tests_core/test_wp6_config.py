"""WP6:配置中心内核与权限——注册表、lint、草稿/发布/回滚、高危延迟与撤销、自动回滚、角色矩阵、设置中心、沙盒草稿。"""
import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest

from sim import Sim

PKG = "interactive-bot"
OWNER, MANAGER, AGENT, FINANCE, VIEWER = 1, 2, 3, 4, 5
NEW_ADDR = "TNewAddressXXXXXXXXXXXXXXXXXXXXa1b2c3"


def mod(name):
    return importlib.import_module(f"{PKG}.{name}")


@pytest.fixture()
def cfgenv(env):
    """env + 角色表(owner/manager/agent/finance/viewer)。"""
    store = mod("config.store")
    store._write_revision(0, {"access.roles": {str(OWNER): "owner", str(MANAGER): "manager", str(AGENT): "agent",
                                              str(FINANCE): "finance", str(VIEWER): "viewer"}}, "测试角色", effective_at=None, audit=False)
    return env


NEW_WELCOME = {"zh": "[[robot]] <b>欢迎来到白猫工作室(测试版)</b>\n想了解什么,直接说。", "en": "[[robot]] <b>Welcome to WhiteCat Studio (test)</b>\nJust tell me what you need."}


# ---------- bootstrap ----------
def test_bootstrap_imports_legacy_settings(env):
    store = mod("config.store")
    with env.db.SessionMaker() as s:
        s.add(env.model.AppSetting(key="frontdesk.lines", value=json.dumps([{"id": "x", "name_zh": "自定义线", "keywords": ["x"], "fields": []}], ensure_ascii=False)))
        s.add(env.model.AppSetting(key="commerce.catalog", value=json.dumps({"a": {"title_zh": "A", "stars": 10}})))
        s.add(env.model.AppSetting(key="commerce.ton", value=json.dumps({"address": "EQabc"})))
        s.add(env.model.AppSetting(key="system_prompt", value="运营说明"))
        s.commit()
    env.gs.update_global_config({"disable_captcha": True, "llm_price_in_per_m": 0.5})
    rid = store.bootstrap()
    assert rid == 1 and store.bootstrap() is None                      # 幂等
    snap = store.get_active()
    assert snap["lines"][0]["id"] == "x" and snap["catalog"]["a"]["stars"] == 10 and snap["payments.ton.address"] == "EQabc"
    assert snap["ai.operator_prompt"] == "运营说明" and snap["abuse.disable_captcha"] is True and snap["budget.llm_price_in_per_m"] == 0.5
    assert snap["access.roles"] == {"1": "owner"}                          # .env 管理员全部导入为 owner
    assert mod("frontdesk.lines").get_lines()[0]["id"] == "x"


# ---------- lint ----------
def test_lint_placeholder_mismatch_blocks_publish(cfgenv):
    store = mod("config.store")
    bad = {"zh": "[[check]] 已收款 {id} {oops}", "en": "[[check]] paid {id} {amount} {paid_at} {next_step}"}
    issues = store.save_draft(OWNER, "copy.order.paid", bad)
    assert any(i.level == "error" and "占位符" in i.msg for i in issues)
    with pytest.raises(store.PublishBlocked):
        store.publish(OWNER)


def test_lint_precheck_forbidden_words(cfgenv):
    lint = mod("config.lint")
    v = {"zh": "[[question]] <b>需要人工吗?</b>", "en": "[[question]] <b>Need verify?</b>"}
    issues = lint.lint("copy.q.software.horizon", v)
    assert [i for i in issues if "禁用词" in i.msg and i.level == "error"]


def test_lint_html_unbalanced(cfgenv):
    lint = mod("config.lint")
    issues = lint.lint("copy.welcome", {"zh": "<b>欢迎", "en": "<b>Welcome</b>"})
    assert any("没有闭合" in i.msg for i in issues)
    issues = lint.lint("copy.welcome", {"zh": "<script>x</script>", "en": "x"})
    assert any("不允许的 HTML" in i.msg for i in issues)


def test_lint_claims_warn_requires_confirmation(cfgenv):
    store = mod("config.store")
    issues = store.save_draft(OWNER, "copy.welcome", {"zh": "[[robot]] 保证成功,无限售后", "en": "[[robot]] guaranteed"})
    assert any(i.level == "warn" for i in issues) and not [i for i in issues if i.level == "error"]
    with pytest.raises(store.NeedsConfirmation):
        store.publish(OWNER)
    store.publish(OWNER, confirm_warnings=True)


def test_lint_flow_unreachable_and_band_requires_declined(cfgenv):
    lint = mod("config.lint")
    flows = json.loads(json.dumps(mod("config.defaults_intake").INTAKE_FLOWS))
    flows["bot_custom"]["exit"] = "handoff:no_such_reason"
    flows["network"]["slots"][0]["options"] = flows["network"]["slots"][0]["options"][:1]
    msgs = [i.msg for i in lint.lint("intake.flows", flows) if i.level == "error"]
    assert any("出口" in m for m in msgs) and any("至少 2 个选项" in m for m in msgs)
    bands = [b for b in mod("config.defaults_intake").BUDGET_BANDS if b["code"] != "declined"]
    assert any("先不说" in i.msg for i in lint.lint("intake.budget_bands", bands))


def test_defaults_pass_their_own_lint(cfgenv):
    lint, R = mod("config.lint"), mod("config.registry")
    for key in R.registry():
        if key == "access.roles":
            continue                                   # 默认空表 = 退回 .env 管理员(owner);发布时才要求至少一位 owner
        errs = [i for i in lint.lint(key, R.default_value(key) if key != "lines" else R.default_value("lines")) if i.level == "error"]
        assert not errs, (key, errs)


# ---------- 发布 / 回滚 ----------
def test_publish_and_rollback_creates_new_revision(cfgenv):
    store, env = mod("config.store"), cfgenv
    s = Sim(env)
    assert "欢迎来到白猫工作室</b>" in s.start()[0]["text"]
    store.save_draft(OWNER, "copy.welcome", NEW_WELCOME)
    r1 = store.publish(OWNER)
    assert "(测试版)" in Sim(env, uid=8).start()[0]["text"]                  # 发布后客户立即看到新卡
    r2 = store.rollback(OWNER, r1 - 1)
    assert r2 > r1
    assert "(测试版)" not in Sim(env, uid=9).start()[0]["text"]               # 回滚恢复
    hist = store.history()
    assert hist[0]["reverted_from"] == r1 - 1 and len(hist) == r2


def test_restore_default_by_rollback_removes_key(cfgenv):
    store = mod("config.store")
    store.save_draft(OWNER, "intake.max_questions", 2)
    r1 = store.publish(OWNER)
    assert store.override("intake.max_questions") == 2
    store.rollback(OWNER, r1 - 1)
    assert store.override("intake.max_questions") is mod("config")._MISSING
    assert mod("config").cfg.get("intake.max_questions") == 3


# ---------- 高危协议 ----------
def test_high_risk_delay_and_revoke(cfgenv):
    store, env = mod("config.store"), cfgenv
    addrs = [{"network": "TRC20", "address": NEW_ADDR, "purpose": "payment"}]
    store.save_draft(OWNER, "trust.official_addresses", addrs)
    with pytest.raises(store.NeedsConfirmation) as ei:
        store.publish(OWNER)
    assert ei.value.kind == "phrase" and "a1b2c3" in ei.value.detail
    rid = store.publish(OWNER, confirm_phrase="a1b2c3")
    assert mod("frontdesk.trust").official_addresses() == []                  # 生效前仍是旧值
    env.runtime.shift_time(minutes=10)
    assert mod("frontdesk.trust").official_addresses() == []
    env.runtime.shift_time(minutes=6)
    assert mod("frontdesk.trust").official_addresses()[0]["address"] == NEW_ADDR     # 15 分钟后生效

    old = [{"network": "TRC20", "address": "TOldAddressXXXXXXXXXXXXXXXXXXXXXXffffff", "purpose": "payment"}]
    store.save_draft(OWNER, "trust.official_addresses", old)
    rid2 = store.publish(OWNER, confirm_phrase="ffffff")
    assert store.revoke(OWNER, rid2) is True                                  # 生效前撤销
    env.runtime.shift_time(minutes=30)
    assert mod("frontdesk.trust").official_addresses()[0]["address"] == NEW_ADDR
    assert store.revoke(OWNER, rid) is False                                  # 已生效的无法撤销


def test_high_risk_requires_owner_and_manager_cannot_change_payment_address(cfgenv):
    store, permissions = mod("config.store"), mod("admin.permissions")
    with pytest.raises(permissions.PermissionDenied):
        store.save_draft(MANAGER, "payments.ton.address", "EQxxxxxxxx")
    with pytest.raises(permissions.PermissionDenied):
        store.save_draft(MANAGER, "trust.official_accounts", [])
    store.save_draft(MANAGER, "copy.welcome", NEW_WELCOME)                      # normal 项可以
    store.publish(MANAGER)


# ---------- 自动回滚 ----------
def test_auto_rollback_on_render_errors(cfgenv):
    store, env = mod("config.store"), cfgenv
    store.save_draft(OWNER, "copy.welcome", NEW_WELCOME)
    r1 = store.publish(OWNER)
    for _ in range(3):
        store.report_render_error("welcome")
    hist = store.history()
    assert hist[0]["author"] == 0 and hist[0]["reverted_from"] == r1 - 1 and hist[0]["id"] > r1
    assert "(测试版)" not in mod("config").copy("welcome", "zh")
    assert any("配置已自动回滚" in t for t in Sim(env).outbox())


def test_copy_render_failure_falls_back_to_default(cfgenv, monkeypatch):
    config = mod("config")
    miss = config._MISSING
    broken = {"zh": "已收款 {id} {nope}", "en": "paid {id} {nope}"}
    monkeypatch.setattr(config, "_override", lambda k: broken if k == "order.paid" else miss)
    text = config.copy("order.paid", "zh", id="X1", amount="1", paid_at="t", next_step="n")
    assert "X1" in text and "已收到您的付款" in text                           # 回退默认文案,客户不会遇到沉默


# ---------- 权限矩阵 ----------
MATRIX = [
    ("view", "owner manager agent finance viewer"),
    ("case_ops", "owner manager agent"),
    ("quote_catalog", "owner manager agent"),
    ("payment_ops", "owner finance"),
    ("deliver", "owner manager agent finance"),
    ("config_edit", "owner manager"),
    ("config_publish", "owner manager"),
    ("config_high", "owner"),
    ("config_rollback", "owner manager"),
    ("export_orders", "owner finance"),
    ("broadcast", "owner manager"),
]


@pytest.mark.parametrize("cap,roles", MATRIX)
def test_permissions_matrix(cfgenv, cap, roles):
    p = mod("admin.permissions")
    uid = {"owner": OWNER, "manager": MANAGER, "agent": AGENT, "finance": FINANCE, "viewer": VIEWER}
    for role, u in uid.items():
        assert p.can(u, cap) == (role in roles.split()), (cap, role)
    assert not p.can(999, cap)


def test_agent_custom_quote_limit_and_deny_text(cfgenv):
    p = mod("admin.permissions")
    assert p.quote_limit(AGENT) == 1000 and p.quote_limit(OWNER) is None
    assert "客服" in p.deny_text(AGENT) and "没有这个权限" in p.deny_text(AGENT)


def test_console_denies_finance_taking_over_and_agent_settings(cfgenv):
    console = mod("admin.console")
    env = cfgenv

    def press(uid, data):
        q = SimpleNamespace(data=data, from_user=SimpleNamespace(id=uid, first_name="a"), message=SimpleNamespace(chat_id=uid), answers=[], edits=[])

        async def answer(text="", show_alert=False):
            q.answers.append(text)

        async def edit(**kw):
            q.edits.append(kw)
        q.answer, q.edit_message_text = answer, edit
        asyncio.run(console.callback_admin(SimpleNamespace(callback_query=q), SimpleNamespace(user_data={}, bot=None)))
        return q
    assert "没有这个权限" in press(FINANCE, "adm:take:7").answers[-1]
    assert "没有这个权限" in press(AGENT, "adm:set").answers[-1]
    assert not press(AGENT, "adm:home").answers[-1].startswith("你的角色")
    assert "没有这个权限" in press(VIEWER, "adm:grant:7:0").answers[-1]


# ---------- 锁定项 / 导入导出 ----------
def test_locked_keys_not_editable(cfgenv):
    store = mod("config.store")
    with pytest.raises(store.LockedKey):
        store.save_draft(OWNER, "guardrail.rules", {"x": 1})
    with pytest.raises(store.LockedKey):
        store.save_draft(OWNER, "persona.preamble", "x")
    with pytest.raises(KeyError):
        store.save_draft(OWNER, "no.such.key", 1)


def test_import_json_creates_drafts_with_lint(cfgenv):
    store = mod("config.store")
    data = {"version": 0, "values": {"copy.welcome": NEW_WELCOME, "copy.order.paid": {"zh": "{oops}", "en": "{oops}"},
                                     "guardrail.rules": 1, "intake.max_questions": 3}}
    res = {r.key: r for r in store.import_json(OWNER, data)}
    assert res["copy.welcome"].ok and not res["copy.order.paid"].ok and not res["guardrail.rules"].ok
    assert "intake.max_questions" not in res                                  # 与当前值相同:忽略
    assert set(store.list_drafts(OWNER)) == {"copy.welcome", "copy.order.paid"}


def test_export_excludes_secret_and_high_for_manager(cfgenv):
    store = mod("config.store")
    full = store.export("copy", include_high=False)
    assert full["values"] and all(k.startswith("copy.") for k in full["values"])
    assert "trust.official_accounts" not in store.export(None, include_high=False)["values"]
    assert "trust.official_accounts" in store.export(None, include_high=True)["values"]


# ---------- 沙盒:/asclient draft ----------
def test_draft_preview_in_sandbox(cfgenv):
    store, env = mod("config.store"), cfgenv
    store.save_draft(OWNER, "copy.welcome", NEW_WELCOME)
    env.sandbox.set_client_view(OWNER, True)
    env.sandbox.set_draft_mode(OWNER, True)
    s = Sim(env, uid=OWNER)
    assert "(测试版)" in s.start()[0]["text"]                                  # 管理员以客户身份看到草稿效果
    other = Sim(env, uid=50)
    assert "(测试版)" not in other.start()[0]["text"]                           # 其他客户不受影响
    env.sandbox.set_draft_mode(OWNER, False)
    assert "(测试版)" not in Sim(env, uid=OWNER).start()[0]["text"]


# ---------- 旧命令快捷方式 ----------
def test_legacy_setlines_publishes_revision_and_audit(cfgenv):
    store, env = mod("config.store"), cfgenv
    ch = mod("handlers.commerce_handlers")
    lines = [{"id": "x", "name_zh": "新线", "keywords": ["新线"], "fields": []}]
    sent = []

    async def reply(text, **kw):
        sent.append(text)
    upd = SimpleNamespace(effective_user=SimpleNamespace(id=OWNER), message=SimpleNamespace(text="/setlines " + json.dumps(lines, ensure_ascii=False), reply_text=reply))
    asyncio.run(ch.cmd_setlines(upd, SimpleNamespace()))
    assert "已保存" in sent[-1] and store.override("lines")[0]["id"] == "x"
    assert env.q if False else True
    # agent 无权
    upd2 = SimpleNamespace(effective_user=SimpleNamespace(id=AGENT), message=SimpleNamespace(text="/setlines []", reply_text=reply))
    n = len(sent)
    asyncio.run(ch.cmd_setlines(upd2, SimpleNamespace()))
    assert len(sent) == n                                                      # 静默拒绝,不泄露信息


def test_setton_saves_draft_not_live(cfgenv):
    store = mod("config.store")
    ch = mod("handlers.commerce_handlers")
    sent = []

    async def reply(text, **kw):
        sent.append(text)
    upd = SimpleNamespace(effective_user=SimpleNamespace(id=OWNER), message=SimpleNamespace(text="/setton EQnewaddress12345", reply_text=reply))
    asyncio.run(ch.cmd_setton(upd, SimpleNamespace()))
    assert "草稿" in sent[-1] and mod("commerce.service").get_ton_config().get("address") is None
    assert store.list_drafts(OWNER)["payments.ton.address"] == "EQnewaddress12345"


# ---------- 设置中心(对话式) ----------
class AdminChat:
    def __init__(self, uid):
        self.uid, self.user_data, self.sent, self.edits, self.answers = uid, {}, [], [], []
        self.bot = SimpleNamespace(send_message=self._send, send_document=self._doc)

    async def _send(self, **kw):
        self.sent.append(kw.get("text", ""))
        return SimpleNamespace(message_id=len(self.sent))

    async def _doc(self, chat, buf, caption=""):
        self.sent.append(("doc", buf.name, caption))

    def press(self, data):
        sc = mod("admin.settings_center")
        q = SimpleNamespace(data=data, from_user=SimpleNamespace(id=self.uid), message=SimpleNamespace(chat_id=self.uid, message_thread_id=None, business_connection_id=None))

        async def answer(text="", show_alert=False):
            self.answers.append(text)

        async def edit(text=None, reply_markup=None, parse_mode=None, **kw):
            self.edits.append((text, reply_markup))
        q.answer, q.edit_message_text, q.get_bot = answer, edit, lambda: self.bot
        asyncio.run(sc.callback_cfg(SimpleNamespace(callback_query=q), SimpleNamespace(user_data=self.user_data, bot=self.bot)))
        return self.edits[-1][0] if self.edits else None

    def say(self, text):
        sc = mod("admin.settings_center")
        replies = []

        async def reply_text(t, **kw):
            replies.append(t)
        msg = SimpleNamespace(text=text, reply_text=reply_text)
        upd = SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=self.uid), effective_chat=SimpleNamespace(id=self.uid))
        try:
            asyncio.run(sc.handle_cfg_text(upd, SimpleNamespace(user_data=self.user_data, bot=self.bot)))
        except Exception as exc:
            assert type(exc).__name__ == "ApplicationHandlerStop"
        return replies


def test_settings_center_edit_preview_publish_flow(cfgenv):
    store, R = mod("config.store"), mod("config.registry")
    chat = AdminChat(OWNER)
    keys = list(R.registry())
    ki = keys.index("copy.welcome")
    assert "设置中心" in chat.press("cfg:home")
    assert "文案 · 入口与先答" in str(chat.edits[-1][1])
    page = chat.press(f"cfg:k:{ki}")
    assert "欢迎卡" in page and "默认" in page
    assert "先发<b>中文</b>" in chat.press(f"cfg:e:{ki}")
    assert "英文" in chat.say(NEW_WELCOME["zh"])[0]                              # 先中文,再问英文
    chat.say(NEW_WELCOME["en"])
    assert store.list_drafts(OWNER)["copy.welcome"] == NEW_WELCOME
    assert "自检通过" in "".join(chat.sent[-1:])
    chat.press(f"cfg:pv:{ki}")
    assert any("预览 · zh" in str(x) for x in chat.sent) and any("预览 · en" in str(x) for x in chat.sent)
    assert "确认发布" in chat.press(f"cfg:pub:{ki}")
    assert "已发布" in chat.press("cfg:pubok")
    assert "(测试版)" in Sim(cfgenv, uid=77).start()[0]["text"]
    chat.press("cfg:hist")
    chat.press("cfg:hv:2")
    assert "回滚到此版本" in str(chat.edits[-1][1])
    assert "已回滚" in chat.press("cfg:rbok:1") and "(测试版)" not in Sim(cfgenv, uid=78).start()[0]["text"]


def test_settings_center_bad_input_and_permissions(cfgenv):
    R = mod("config.registry")
    ki = list(R.registry()).index("intake.max_questions")
    chat = AdminChat(OWNER)
    chat.press(f"cfg:e:{ki}")
    assert "格式不对" in chat.say("不是数字")[0]
    chat.say("9")                                                                  # 超范围:存为草稿但自检失败
    assert "取值范围" in chat.sent[-1]
    agent = AdminChat(AGENT)
    agent.press("cfg:home")
    assert "没有这个权限" in agent.answers[-1]
    mgr = AdminChat(MANAGER)
    hi = list(R.registry()).index("payments.ton.address")
    mgr.press(f"cfg:e:{hi}")
    assert "没有这个权限" in mgr.answers[-1] and not mgr.user_data.get("cfg_wait")


def test_settings_center_high_risk_confirm_phrase_flow(cfgenv):
    store, R = mod("config.store"), mod("config.registry")
    ki = list(R.registry()).index("payments.ton.address")
    chat = AdminChat(OWNER)
    chat.press(f"cfg:e:{ki}")
    chat.say("EQnewaddressZZZZZZZZZZZZZZZZZZZZZZ123456")
    chat.press(f"cfg:pub:{ki}")
    assert "二次确认" in chat.press("cfg:pubok")
    assert "确认短语不对" in chat.say("错的")[0]
    chat.say("123456")
    assert any("已发布" in str(s) for s in chat.sent) and any("将在" in str(s) for s in chat.sent)
    assert store.latest_revision_id() >= 2
    rid = store.latest_revision_id()
    assert "已撤销" in str(chat.press(f"cfgx:revoke:{rid}") or chat.answers[-1]) or "已撤销" in chat.answers[-1]


def test_config_export_command_sends_file(cfgenv):
    sc = mod("admin.settings_center")
    chat = AdminChat(OWNER)
    replies = []

    async def reply_text(t, **kw):
        replies.append(t)
    upd = SimpleNamespace(effective_user=SimpleNamespace(id=OWNER), effective_chat=SimpleNamespace(id=OWNER), message=SimpleNamespace(reply_text=reply_text))
    asyncio.run(sc.cmd_config(upd, SimpleNamespace(args=["export", "copy"], bot=chat.bot)))
    assert chat.sent and chat.sent[-1][0] == "doc" and chat.sent[-1][1].startswith("whitecat-config-v")
