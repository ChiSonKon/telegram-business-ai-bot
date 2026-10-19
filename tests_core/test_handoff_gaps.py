# -*- coding: utf-8 -*-
"""测试 WP8 遗留缺口补齐与 TON 官方收款地址核验。
包含:
1. Business 客户报价时发送 biz.bind_bot 并在管理群提示; 随后客户 /start ord_<id> 顺利激活订单卡。
2. quote.confirm_above_u / quote.agent_custom_max_u / payments.tolerance / payments.quote_valid_hours / kb.known_issues 注册进配置中心并受 lint 约束。
3. TON 官方收款地址默认值与核验。
"""
import asyncio
import importlib
from types import SimpleNamespace
import pytest

from sim import SimBot
from test_wp8_gmpay import gw  # noqa: F401


def run(c):
    return asyncio.run(c)


def H(name):
    return importlib.import_module(f"interactive-bot.{name}")


def test_business_customer_bind_bot_and_start_ord(env, gw):
    bot = SimBot()
    user_id = 88888
    chat_id = 99999
    biz_conn_id = "biz_conn_123"

    # 1. 模拟客户为 BusinessContact, 且 bot_started=False
    with env.db.SessionMaker() as s:
        s.add(env.model.BusinessContact(
            user_id=user_id,
            chat_id=chat_id,
            business_connection_id=biz_conn_id,
            first_name="BizUser",
        ))
        s.commit()

    profile = H("frontdesk.profile")
    assert not profile.bot_started(user_id)

    # 2. 发送自定义报价
    gh = H("handlers.gateway_handlers")
    order, ok = run(gh.send_custom_quote(
        bot, user_id, title="双向智能客服定制", detail="全功能版本", amount="288", next_step="开工", created_by=1
    ))
    assert ok is True
    assert order.id

    # 验证 Business 客户收到 biz.bind_bot 卡片, 包含到 bot 私聊的深链
    biz_msgs = [o for o in bot.outs if o["kw"].get("chat_id") == chat_id]
    assert len(biz_msgs) >= 1
    bind_msg = biz_msgs[0]
    assert "订单和到账通知会发在白猫官方机器人里" in bind_msg["text"]
    # 验证按钮 URL 带有 start=ord_<id>
    btn = bind_msg["markup"].inline_keyboard[0][0]
    assert f"start=ord_{order.id}" in btn.url

    # 验证管理群收到通知
    admin_group_id = env.gs.get_admin_group_id()
    admin_msgs = [o for o in bot.outs if str(o["kw"].get("chat_id")) == str(admin_group_id)]
    assert any("客户尚未在官方机器人启动私聊" in o["text"] for o in admin_msgs)

    # 3. 客户点击按钮打开机器人私聊触发 /start ord_<id>
    cmd = H("handlers.command_handlers")
    user = SimpleNamespace(id=user_id, first_name="BizUser", last_name=None, username="bizuser")
    update = SimpleNamespace(
        effective_user=user,
        effective_chat=SimpleNamespace(id=user_id),
        message=SimpleNamespace(text=f"/start ord_{order.id}"),
    )
    context = SimpleNamespace(bot=bot, args=[f"ord_{order.id}"])

    run(cmd.start(update, context))

    # 验证 profile.bot_started 被激活为 True
    assert profile.bot_started(user_id) is True

    # 验证在私聊 (chat_id=user_id) 中发送了选网络订单卡
    private_msgs = [o for o in bot.outs if o["kw"].get("chat_id") == user_id]
    assert any(order.id in o["text"] and "请选择付款网络" in o["text"] for o in private_msgs)

    # 4. 客户选网络 (TRON)
    class FakeQ:
        def __init__(self, data, uid):
            self.data = data
            self.from_user = SimpleNamespace(id=uid, full_name="BizUser")
            self.message = SimpleNamespace(chat_id=uid)
        async def answer(self, *a, **k): pass
        async def edit_message_text(self, *a, **k): pass

    fq = FakeQ(f"pay:gm:{order.id}:tron", user_id)
    run(gh.callback_gateway(SimpleNamespace(callback_query=fq), context))

    # 选网络生成收款信息后置顶
    assert any(p.get("chat_id") == user_id for p in bot.api_calls("pin_chat_message"))




def test_new_config_registry_keys_and_lint(env):
    reg = H("config.registry")
    lint = H("config.lint").lint

    # 验证新增的 5 个键已成功注册
    for key in (
        "quote.confirm_above_u",
        "quote.agent_custom_max_u",
        "payments.tolerance",
        "payments.quote_valid_hours",
        "kb.known_issues",
        "payments.ton.address",
    ):
        ck = reg.get_key(key)
        assert ck is not None, f"Key {key} not registered!"
        # 默认值必须通过 lint 自检
        issues = lint(key, ck.default)
        errs = [str(i) for i in issues if i.level == "error"]
        assert not errs, f"Default value for {key} failed lint: {errs}"

    # 验证 tolerance 容差非法值被 lint 阻止
    bad_tol = lint("payments.tolerance", {"abs_u": "-1", "pct": "abc"})
    assert any("必须是非负数字" in str(i) for i in bad_tol)

    # 验证 known_issues 缺少 id 被阻止
    bad_kb = lint("kb.known_issues", [{"line": "software"}])
    assert any("缺少 id 或 card_key" in str(i) for i in bad_kb)


def test_ton_address_default_is_empty_unless_env_set(env):
    """开源版:不内置任何收款地址,避免 fork 的订单误付到作者钱包;由 TON_ADDRESS 或配置中心提供。"""
    reg = H("config.registry")
    ck = reg.get_key("payments.ton.address")
    assert ck.default == "" and "UQ" not in str(ck.default)
