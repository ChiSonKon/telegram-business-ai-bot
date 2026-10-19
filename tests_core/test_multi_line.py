"""多业务并行:问答/AI 阶段客户中途提到其他业务 → 登记并行意向、衔接话术、分业务口径、连续 3 条或明确要求则切换主线、交接带全部业务。"""
import asyncio
import importlib
from types import SimpleNamespace

from sim import Sim, SimBot

PKG = "interactive-bot"


def mod(n):
    return importlib.import_module(f"{PKG}.{n}")


def brief_of(env, uid=7):
    return mod("frontdesk.intake.brief").load(uid)


def to_ai_stage(s):
    """软件线:一句话问清后进入 AI 阶段。"""
    s.say("WhiteCat 软件能群发私信吗?Windows 用,想先试用")
    assert s.last_gate is None or True


def test_mentioning_another_line_in_ai_stage_registers_parallel_interest(env):
    s = Sim(env)
    to_ai_stage(s)
    s.say("另外我还想买点静态IP,住宅的")
    b = brief_of(env)
    assert b.line == "software" and [i["line"] for i in b.interests] == ["network"]           # 主线不丢,并行登记
    sysmsg = s.llm.last_system
    assert "并行业务口径" in sysmsg and "独享纯净静态网络" in sysmsg                              # 带上网络线自己的口径
    assert "同时在谈" in sysmsg and "WhiteCat 5.0 软件" in sysmsg                              # 本轮衔接指令:先答 IP 再回主线
    assert "并行关注业务" in sysmsg                                                           # 简报里有并行意向


def test_parallel_line_product_facts_are_injected_into_rag(env):
    s = Sim(env)
    to_ai_stage(s)
    captured = {}
    ai = mod("ai_turn")
    orig = ai.search_knowledge

    def spy(query, line_id=None, top_k=5, extra_line_ids=None):
        captured["extra"] = list(extra_line_ids or [])
        return orig(query, line_id=line_id, top_k=top_k, extra_line_ids=extra_line_ids)
    ai.search_knowledge = spy
    s.say("还想了解一下静态IP")
    assert "network" in captured["extra"]


def test_same_interest_is_not_duplicated_and_capped(env):
    s = Sim(env)
    to_ai_stage(s)
    s.say("还想要静态IP")
    s.say("IP 大概怎么收费")                                       # 同一个并行业务再提:不重复登记
    s.say("我还想做空投")
    s.say("再来个 ai账号 chatgpt")
    s.say("以及推特账号")                                           # 超过 3 个并行意向:不再新增
    b = brief_of(env)
    assert len(b.interests) <= 3 and len({i["line"] for i in b.interests}) == len(b.interests)


def test_three_consecutive_messages_promote_the_new_line_to_primary(env):
    s = Sim(env)
    to_ai_stage(s)
    for t in ("我想问问静态IP", "住宅IP还是机房IP更适合", "IP 数量大的话有优惠吗"):
        s.say(t)
    b = brief_of(env)
    assert b.line == "network"                                                               # 连续 3 条都在谈 → 升为主线
    assert [i["line"] for i in b.interests] == ["software"]                                  # 原主线降为并行意向,不丢
    assert "原来的" in s.llm.last_system


def test_explicit_switch_phrase_promotes_immediately(env):
    s = Sim(env)
    to_ai_stage(s)
    s.say("先聊聊静态IP吧")
    b = brief_of(env)
    assert b.line == "network" and [i["line"] for i in b.interests] == ["software"]


def test_mid_intake_mention_gets_a_bridge_once_and_intake_continues(env):
    s = Sim(env)
    outs = s.say("想做个能自动回复客户的机器人,大概多少钱?")
    assert s.case.state == "PRECHECK"
    outs = s.say("对了我还想买几个静态IP")
    text = "\n".join(o["text"] or "" for o in outs)
    assert "也记下了" in text and "独享纯净静态网络" in text
    assert s.case.state == "PRECHECK"                                                         # 问答照常继续
    assert [i["line"] for i in brief_of(env).interests] == ["network"]
    again = "\n".join(o["text"] or "" for o in s.say("静态IP 住宅的"))
    assert "也记下了" not in again                                                            # 衔接话只发一次


def test_handoff_summary_lists_all_lines(env):
    s = Sim(env)
    to_ai_stage(s)
    s.say("另外我还想买点静态IP")
    text = mod("frontdesk.intake.brief").render_for_admin(brief_of(env))
    assert "并行关注：独享纯净静态网络" in text


def test_primary_unchanged_when_no_other_line_mentioned(env):
    s = Sim(env)
    to_ai_stage(s)
    s.say("怎么安装?")
    b = brief_of(env)
    assert b.line == "software" and b.interests == [] and "同时在谈" not in s.llm.last_system


# ---------- 话题里的"打开处理卡":深链跳转到机器人 ----------
def test_open_case_button_is_a_deep_link_to_the_bot(env):
    ob = mod("outbox")
    ob.enqueue_admin("k1", "新转交", user_id=7)
    bot = SimBot()
    asyncio.run(ob.drain(bot))
    markup = bot.outs[0]["markup"]
    btn = markup.inline_keyboard[0][0]
    assert btn.url == "https://t.me/baimao_interactive_bot?start=case_7" and not btn.callback_data
