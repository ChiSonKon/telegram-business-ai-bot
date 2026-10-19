"""交接简报 HandoffBrief(03 §5.5):问答与 AI 共用的结构化事实。存 frontdesk_case.brief_json。

渲染两份:给 AI(注入 system,不是客户原话)、给同事(中文要点,管理群/客户处理卡)。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from db.database import SessionMaker
from db.model import FrontdeskCase


@dataclass
class HandoffBrief:
    v: int = 1
    case_id: int | None = None
    channel: str = "bot"
    lang: str = "zh"
    source: str | None = None
    returning: bool = False
    prior_orders: list = field(default_factory=list)
    role: str | None = None
    line: str | None = None
    slots: dict = field(default_factory=dict)
    slot_sources: dict = field(default_factory=dict)
    budget: dict = field(default_factory=lambda: {"band": None, "stated": None, "asked": False, "declined": False})
    signals: dict = field(default_factory=dict)
    pending_questions: list = field(default_factory=list)
    first_message: str = ""
    missing: list = field(default_factory=list)
    escalation: dict | None = None
    interests: list = field(default_factory=list)      # 并行意向:[{line,name_zh,name_en,first_text,last_text}]
    focus: str | None = None                           # 最近在谈的并行业务线
    focus_streak: int = 0                              # 连续几条在谈它(够 3 条升为主线)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | None) -> "HandoffBrief":
        try:
            data = json.loads(raw or "{}")
        except ValueError:
            data = {}
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


# ---------- 存取 ----------
def _latest(db, user_id: int, owner_id: int = 0):
    return db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()


def load(user_id: int, owner_id: int = 0) -> HandoffBrief:
    with SessionMaker() as db:
        c = _latest(db, user_id, owner_id)
        return HandoffBrief.from_json(c.brief_json) if c else HandoffBrief()


def save(user_id: int, brief: HandoffBrief, owner_id: int = 0) -> None:
    with SessionMaker() as db:
        c = _latest(db, user_id, owner_id)
        if c:
            c.brief_json = brief.to_json()
            db.commit()


def update(user_id: int, owner_id: int = 0, **changes) -> HandoffBrief:
    """读-改-写简报的若干字段(slots/signals 等字典按键合并)。"""
    b = load(user_id, owner_id)
    for k, v in changes.items():
        cur = getattr(b, k, None)
        if isinstance(cur, dict) and isinstance(v, dict):
            cur.update(v)
        else:
            setattr(b, k, v)
    save(user_id, b, owner_id)
    return b


# ---------- 渲染 ----------
_SIGNAL_ZH = {"urgency": {"urgent": "着急"}, "sentiment": {"angry": "情绪激动", "anxious": "焦虑"}}
_BOOL_ZH = {"trust_concern": "有信任疑虑", "company": "企业/对公", "reseller": "分销意向", "competitor_compare": "在比价"}
BAND_ZH = {"<300": "300U 以内", "300-800": "300–800U", "800-2000": "800–2000U", "2000+": "2000U 以上", "declined": "先不说"}


def _slot_label(flow_slots: dict, key: str, lang: str) -> str:
    s = flow_slots.get(key) or {}
    return (s.get("label") or {}).get(lang) or key


def _value_text(flow_slots: dict, key: str, code, lang: str) -> str:
    s = flow_slots.get(key) or {}
    if s.get("kind") == "band":
        for b in _bands():
            if b["code"] == code:
                return b.get(lang) or b["zh"]
    for o in s.get("options", []) or []:
        if o["code"] == code:
            return o.get(lang) or o["zh"]
    return str(code)


def _bands() -> list[dict]:
    from ...config import cfg
    return cfg.get("intake.budget_bands", default=[])


def _flow_slots(flow_id: str | None) -> dict:
    from ...config import cfg
    flows = cfg.get("intake.flows", default={})
    return {s["key"]: s for s in (flows.get(flow_id or "") or {}).get("slots", [])}


def render_for_admin(b: HandoffBrief, flow_id: str | None = None, line_name: str | None = None) -> str:
    """同事看的中文要点,一屏。"""
    slots = _flow_slots(flow_id or b.line)
    out = []
    head = [x for x in (f"来源 {b.source}" if b.source else "", f"业务线 {line_name or b.line}" if (line_name or b.line) else "",
                        "回访客户" if b.returning else "") if x]
    if head:
        out.append(" · ".join(head))
    for k, v in b.slots.items():
        if k == "budget":
            continue
        out.append(f"• {_slot_label(slots, k, 'zh')}：{_value_text(slots, k, v, 'zh')}")
    bd = b.budget
    if bd.get("declined"):
        out.append("• 预算：客户表示暂不透露")
    elif bd.get("band"):
        out.append(f"• 预算：{BAND_ZH.get(bd['band'], bd['band'])}")
    elif bd.get("stated"):
        out.append(f"• 预算：自述 {bd['stated']}")
    sig = []
    for k, m in _SIGNAL_ZH.items():
        if b.signals.get(k) in m:
            sig.append(m[b.signals[k]])
    sig += [zh for k, zh in _BOOL_ZH.items() if b.signals.get(k)]
    if sig:
        out.append("信号：" + "、".join(sig))
    if b.pending_questions:
        out.append("待回答的问题：" + "；".join(q[:40] for q in b.pending_questions[:3]))
    for it in b.interests:
        out.append(f"并行关注：{it.get('name_zh') or it['line']}（原话：{(it.get('first_text') or '')[:60]}）")
    if b.first_message:
        out.append(f"客户原话：{b.first_message[:120]}")
    return "\n".join(out)


def render_for_ai(b: HandoffBrief, lang: str = "zh", flow_id: str | None = None) -> str:
    """注入 system 的接待简报。明确标注"系统整理,不是客户原话"。"""
    slots = _flow_slots(flow_id or b.line)
    zh = lang != "en"
    out = ["【接待简报 · 系统根据客户作答整理,不是客户原话】" if zh else "[Intake brief · compiled by the system from the customer's answers — not their own words]"]
    if b.line:
        out.append((f"业务线:{b.line}" if zh else f"Line: {b.line}"))
    if b.returning:
        out.append("这是回访客户,请自然衔接上次的话题。" if zh else "Returning customer — pick up naturally from last time.")
    for k, v in b.slots.items():
        out.append(f"- {k}({_slot_label(slots, k, lang)}): {_value_text(slots, k, v, lang)}")
    bd = b.budget
    if bd.get("declined"):
        out.append("- 预算:客户已表示暂不透露预算,不要再询问。" if zh else "- Budget: the customer chose not to share it — do not ask again.")
    elif bd.get("band"):
        out.append(f"- budget.band={bd['band']}")
    elif bd.get("stated"):
        out.append(f"- budget.stated={bd['stated']}")
    if b.signals:
        out.append("- 信号:" + json.dumps(b.signals, ensure_ascii=False) if zh else "- Signals: " + json.dumps(b.signals))
    for it in b.interests:
        out.append((f"- 并行关注业务:{it.get('name_zh') or it['line']}(客户原话:{(it.get('first_text') or '')[:60]})" if zh
                    else f"- Also interested in: {it.get('name_en') or it['line']} (customer said: {(it.get('first_text') or '')[:60]})"))
    if b.pending_questions:
        out.append(("- 客户尚未得到回答的问题(先简短回答):" if zh else "- Questions still unanswered (answer these first, briefly): ") + "; ".join(b.pending_questions))
    return "\n".join(out)
