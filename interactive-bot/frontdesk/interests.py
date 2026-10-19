"""多业务并行(丝滑过渡):客户在一项业务的过程中又提到别的业务时,不丢进度、不乱切换。

- 主线 = 当前业务线(case.answers.line / 简报 line);客户另外提到的业务登记为「并行意向」(最多 3 个),写入简报。
- 问答阶段:登记后发一句衔接话(只发一次),问答照常往下走;问答完成后 AI 会带着所有意向接着聊。
- AI 阶段:本轮若在谈某个并行意向,AI 先用该业务的口径简短回答,再一句话衔接回主线/询问是否一并推进;
  系统提示词里同时带上各业务的口径与剧本"禁止"项,检索也同时注入各业务的产品资料。
- 切换主线:客户连续 3 条都在谈同一个并行业务,或明确说"先聊/改聊/switch to X" → 该业务升为主线,原主线降为并行意向。
- 交接给同事时,简报里列出所有业务与各自已知信息。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from db.database import SessionMaker
from db.model import FrontdeskCase

from . import lines as biz_lines
from .intake import brief as brief_mod

logger = logging.getLogger(__name__)

MAX_INTERESTS = 3
PROMOTE_STREAK = 3
_SWITCH = re.compile(r"先聊|先说|先看|先问|改聊|换成|换个话题|聊聊|talk about|switch to|let'?s (?:talk|discuss)|first[, ]+(?:about|let)", re.I)


@dataclass
class Tracked:
    primary: dict | None = None            # 主线业务线(注册表里的 dict)
    focus: dict | None = None              # 本条消息主要在谈的业务线(没提到别的业务时 = 主线)
    new_interest: bool = False
    promoted: bool = False
    previous: dict | None = None           # 切换主线时,原来的主线
    others: list = field(default_factory=list)   # 其余并行意向的业务线 dict


def _line_by_id(line_id: str | None) -> dict | None:
    return next((x for x in biz_lines.get_lines() if x["id"] == line_id), None)


def _name(ln: dict | None, lang: str) -> str:
    if not ln:
        return ""
    return (ln.get("name_en") if lang == "en" else ln.get("name_zh")) or ln.get("name_zh") or ln.get("id", "")


def primary_line_id(user_id: int, owner_id: int = 0) -> str | None:
    b = brief_mod.load(user_id, owner_id)
    if b.line:
        return b.line
    with SessionMaker() as db:
        c = db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        if not c:
            return None
        try:
            ans = json.loads(c.answers_json or "{}")
        except ValueError:
            ans = {}
        return ans.get("line") or (ans.get("slots") or {}).get("line") or (ans.get("slots") or {}).get("product")


def detect(text: str, primary_id: str | None) -> dict | None:
    """这条消息是否提到了「主线以外」的业务线。"""
    hit = biz_lines.match_line(text)
    if not hit or hit["id"] in ("other_biz", primary_id):
        return None
    return hit


def _emit(event: str, user_id: int, line: str | None) -> None:
    try:
        from ..analytics.events import emit
        emit(event, user_id, line=line)
    except Exception:
        pass


def register(user_id: int, hit: dict, text: str, owner_id: int = 0) -> bool:
    """登记并行意向;返回是否为新增。"""
    b = brief_mod.load(user_id, owner_id)
    for it in b.interests:
        if it["line"] == hit["id"]:
            it["last_text"] = text[:120]
            brief_mod.save(user_id, b, owner_id)
            return False
    if len(b.interests) >= MAX_INTERESTS:
        return False
    b.interests.append({"line": hit["id"], "name_zh": hit.get("name_zh", hit["id"]), "name_en": hit.get("name_en", hit["id"]),
                        "first_text": text[:120], "last_text": text[:120]})
    brief_mod.save(user_id, b, owner_id)
    _emit("interest_added", user_id, hit["id"])
    return True


def _set_primary(user_id: int, new_id: str, old_id: str | None, owner_id: int = 0) -> None:
    """新主线写入 case.answers.line 与简报;旧主线降为并行意向。"""
    with SessionMaker() as db:
        c = db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()
        if c:
            try:
                ans = json.loads(c.answers_json or "{}")
            except ValueError:
                ans = {}
            ans["line"] = new_id
            c.answers_json = json.dumps(ans, ensure_ascii=False)
            db.commit()
    b = brief_mod.load(user_id, owner_id)
    b.interests = [i for i in b.interests if i["line"] != new_id]
    old = _line_by_id(old_id)
    if old and old["id"] != "other_biz" and not any(i["line"] == old["id"] for i in b.interests):
        b.interests.append({"line": old["id"], "name_zh": old.get("name_zh", old["id"]), "name_en": old.get("name_en", old["id"]),
                            "first_text": b.first_message[:120], "last_text": ""})
    b.line, b.focus, b.focus_streak = new_id, None, 0
    brief_mod.save(user_id, b, owner_id)


def track(user_id: int, text: str, owner_id: int = 0) -> Tracked:
    """AI 阶段每条客户消息调用:确定主线与本条焦点,必要时登记并行意向/切换主线。"""
    primary_id = primary_line_id(user_id, owner_id)
    if not primary_id:                                              # 还没有主线:沿用原有解析(首次命中即定为主线)
        ln = biz_lines.resolve(user_id, text, owner_id)
        return Tracked(primary=ln, focus=ln)
    hit = detect(text, primary_id)
    b = brief_mod.load(user_id, owner_id)
    if not hit:
        b.focus_streak = 0 if b.focus is None else b.focus_streak
        others = [x for x in (_line_by_id(i["line"]) for i in b.interests) if x]
        return Tracked(primary=_line_by_id(primary_id), focus=_line_by_id(b.focus) if b.focus and b.focus_streak else _line_by_id(primary_id), others=others)
    new = register(user_id, hit, text, owner_id)
    b = brief_mod.load(user_id, owner_id)
    b.focus_streak = b.focus_streak + 1 if b.focus == hit["id"] else 1
    b.focus = hit["id"]
    explicit = bool(_SWITCH.search(text))
    promoted = explicit or b.focus_streak >= PROMOTE_STREAK
    brief_mod.save(user_id, b, owner_id)
    old_primary = primary_id
    if promoted:
        _set_primary(user_id, hit["id"], primary_id, owner_id)
        b = brief_mod.load(user_id, owner_id)
        primary_id = hit["id"]
    others = [x for x in (_line_by_id(i["line"]) for i in b.interests) if x]
    return Tracked(primary=_line_by_id(primary_id), focus=hit, new_interest=new, promoted=promoted, others=others,
                   previous=_line_by_id(old_primary) if promoted else None)


# ---------- 给 AI 的材料 ----------
def line_briefs(others: list[dict], lang: str) -> str:
    """并行业务的口径(业务线说明里的价格/禁忌),紧凑版。"""
    parts = []
    for ln in others:
        notes = ln.get("notes_en" if lang == "en" else "notes_zh") or ""
        parts.append(f"- {_name(ln, lang)}: {notes}" if notes else f"- {_name(ln, lang)}")
    if not parts:
        return ""
    return ("【并行业务口径】客户同时关注以下业务,各业务按各自口径回答,不要混用价格与话术:\n" if lang != "en"
            else "[Parallel lines] The customer is also interested in the lines below — answer each by its own notes; never mix prices or pitches:\n") + "\n".join(parts)


def playbook_musts(others: list[dict], lang: str) -> str:
    """并行业务各自剧本里的"禁止"项。"""
    from ..ai import playbook
    out = []
    for ln in others:
        pb = playbook.load(ln["id"])
        mn = ((pb or {}).get("must_not") or {}).get(lang) or ((pb or {}).get("must_not") or {}).get("zh") or []
        if mn:
            out.append(f"- {_name(ln, lang)}: " + ("; ".join(mn)))
    if not out:
        return ""
    return ("【并行业务禁止】\n" if lang != "en" else "[Parallel-line prohibitions]\n") + "\n".join(out)


def turn_rules(tr: Tracked, brief: brief_mod.HandoffBrief, lang: str) -> list[str]:
    zh = lang != "en"
    rules = []
    primary, focus = _name(tr.primary, lang), _name(tr.focus, lang)
    if tr.promoted:
        rules.append((f"客户把话题转到了【{focus}】,现在以它为主线继续;原来的【{_name(tr.previous, lang)}】已记为并行意向,自然地提一句稍后可以接着聊。" if zh else
                      f"The customer moved on to [{focus}] — treat it as the main topic now; the earlier topic stays as a parallel interest, mention briefly that we can pick it up later."))
    elif tr.focus and tr.primary and tr.focus["id"] != tr.primary["id"]:
        rules.append((f"客户这条消息同时在谈【{focus}】(主线是【{primary}】)。先按【{focus}】的口径、依据实时业务数据简短回答他问到的部分,"
                      f"再用一句话衔接:问他是否一并推进,或先回到【{primary}】。不要丢掉主线已收集的信息。" if zh else
                      f"This message is also about [{focus}] (main topic: [{primary}]). First answer the [{focus}] part briefly using its own notes and the live data, "
                      f"then bridge in one sentence: ask whether to move both forward or return to [{primary}]. Keep everything already collected for the main topic."))
    pending = [i for i in brief.interests if not tr.focus or i["line"] != tr.focus["id"]]
    if pending:
        names = "、".join(i.get("name_zh" if zh else "name_en") or i["line"] for i in pending)
        rules.append((f"客户还关注:{names}。主线进展到合适节点时,自然问一句是否接着聊这些;不要同时抛出多个问题。" if zh else
                      f"The customer is also interested in: {names}. At a natural point in the main thread, ask whether to continue with those; never ask several questions at once."))
    return rules

