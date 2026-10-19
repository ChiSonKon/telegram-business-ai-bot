"""真机反馈修复:待跟进"已处理却还显示"、同事回话即接手、多管理员(直接发用户名)、AI 失败提醒。"""
import asyncio
import importlib
from types import SimpleNamespace

import pytest

from sim import SimBot

PKG = "interactive-bot"


def run(c):
    return asyncio.run(c)


def mod(name):
    return importlib.import_module(f"{PKG}.{name}")


def add_case(env, uid, state, hours_ago=0):
    with env.db.SessionMaker() as s:
        c = env.model.FrontdeskCase(user_id=uid, business_owner_id=0, state=state, handoff_reason="explicit_human", role="support")
        s.add(c)
        s.commit()
        return c.id


# ---------- 待跟进 ----------
def test_queue_lists_each_customer_once_and_resolve_closes_all_old_cases(env):
    old = add_case(env, 7, "HUMAN_PENDING")
    new = add_case(env, 7, "HUMAN_PENDING")
    q, actions = mod("admin.queries"), mod("admin.actions")
    assert [x["uid"] for x in q.handoff_queue()] == [7]                       # 旧 case 不再让同一客户重复出现
    assert actions.resolve(1, 7) is True
    assert q.handoff_queue() == []                                            # 标记已解决后不会再冒出来
    with env.db.SessionMaker() as s:
        assert {c.state for c in s.query(env.model.FrontdeskCase).filter_by(user_id=7)} == {"RESOLVED"}


def test_old_pending_case_hidden_when_newer_case_exists(env):
    add_case(env, 7, "HUMAN_PENDING")
    add_case(env, 7, "RESOLVED")                                              # 最新一条已解决,历史遗留的旧待跟进不应显示
    assert mod("admin.queries").handoff_queue() == []


def test_staff_reply_takes_over_pending_case_even_when_chat_mode_is_human(env):
    add_case(env, 7, "HUMAN_PENDING")
    sm = mod("middleware.state_machine")
    sm.set_chat_mode(7, sm.ChatMode.HUMAN_MODE)                               # 转交时聊天模式已是人工:旧逻辑不会触发接管
    with env.db.SessionMaker() as s:
        u = s.query(env.model.User).filter_by(user_id=7).first()
        u.message_thread_id = 900
        s.commit()
    ah = mod("handlers.admin_handlers")
    ah.db.expire_all() if hasattr(ah.db, "expire_all") else None
    sent = []

    class Chat:
        async def send_copy(self, *a, **k):
            sent.append(1)
            return SimpleNamespace(message_id=5)

    class Bot:
        async def get_chat(self, _):
            return Chat()

        async def set_message_reaction(self, **kw):
            pass

    async def reply_html(*a, **k):
        pass
    msg = SimpleNamespace(message_thread_id=900, forum_topic_created=None, forum_topic_closed=None, forum_topic_reopened=None,
                          reply_to_message=None, media_group_id=None, text="您好,我来处理", caption=None, message_id=77, id=77,
                          chat=SimpleNamespace(id=-100), reply_html=reply_html)
    upd = SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=1, first_name="a"), effective_chat=SimpleNamespace(id=-100))
    run(ah.forwarding_message_a2u(upd, SimpleNamespace(bot=Bot(), user_data={}, bot_data={})))
    assert env.service.case_state(7) == "HUMAN_ACTIVE"                         # 同事一回话就算接手,不再卡在待跟进


# ---------- 多管理员:直接发用户名 ----------
@pytest.fixture()
def perm(env):
    return mod("admin.permissions")


def test_parse_admin_line(perm):
    assert perm.parse_admin_line("@alice") == ("alice", "agent")
    assert perm.parse_admin_line("@Bob_01 主管") == ("Bob_01", "manager")
    assert perm.parse_admin_line("https://t.me/carol 财务") == ("carol", "finance")
    assert perm.parse_admin_line("123456789 viewer") == (123456789, "viewer")
    for bad in ("", "@ab", "@alice 老板", "bad name!"):
        with pytest.raises(ValueError):
            perm.parse_admin_line(bad)


def test_owner_adds_known_user_by_username_effective_immediately(env, perm):
    with env.db.SessionMaker() as s:
        s.add(env.model.User(user_id=555, first_name="Alice", username="Alice_Op", chat_mode="AI_MODE"))
        s.commit()
    status, uid = perm.add_admin(1, "alice_op", "manager")                    # 大小写不敏感
    assert (status, uid) == ("added", 555)
    assert perm.role_of(555) == "manager" and perm.is_admin(555) and perm.can(555, "config_edit") and not perm.can(555, "config_high")
    from importlib import import_module
    assert 555 in import_module(f"{PKG}").admin_user_ids                      # 旧代码里直接判断 admin_user_ids 的地方也认他


def test_unknown_username_becomes_pending_and_activates_on_first_contact(env, perm):
    assert perm.add_admin(1, "newbie_x", "agent") == ("pending", None)
    assert perm.role_of(777) is None
    granted = perm.claim_pending(SimpleNamespace(id=777, username="Newbie_X"))
    assert granted == "agent" and perm.role_of(777) == "agent"
    assert perm.claim_pending(SimpleNamespace(id=777, username="Newbie_X")) is None      # 只生效一次


def test_only_owner_can_manage_and_last_owner_is_protected(env, perm):
    perm.add_admin(1, 600, "manager")
    with pytest.raises(perm.PermissionDenied):
        perm.add_admin(600, 601, "agent")                                     # 主管不能加管理员
    with pytest.raises(ValueError):
        perm.set_role(1, 1, "agent")                                          # .env 业主不能降级
    with pytest.raises(ValueError):
        perm.remove_role(1, 1)
    perm.remove_role(1, 600)
    assert perm.role_of(600) is None
    assert 600 not in importlib.import_module(f"{PKG}").admin_user_ids


def test_roles_page_has_add_button_for_owner_only(env, perm):
    views = mod("admin.views")
    perm.add_admin(1, 600, "agent")
    text, markup = views.roles_page(perm._effective_map(), True, {}, {})
    cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "adm:roleadd" in cbs and "adm:role:600" in cbs
    _, markup2 = views.roles_page(perm._effective_map(), False, {}, {})
    assert "adm:roleadd" not in [b.callback_data for row in markup2.inline_keyboard for b in row]


# ---------- AI 失败提醒 ----------
def test_llm_failure_alerts_admin_group_once_per_half_hour_without_leaking_keys(env):
    ai = mod("ai_turn")
    err = RuntimeError("Error code: 401 - Authentication Fails, Your api key: sk-ABCDEF1234567890XYZ is invalid")
    ai._alert_llm_failure(err)
    ai._alert_llm_failure(err)                                               # 同半小时只提醒一次
    with env.db.SessionMaker() as s:
        rows = s.query(env.model.Outbox).all()
    assert len(rows) == 1 and "AI 调用失败" in rows[0].text and "401" in rows[0].text
    assert "ABCDEF1234567890XYZ" not in rows[0].text                         # 密钥脱敏
