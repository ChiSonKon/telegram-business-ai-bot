"""数据驱动问答引擎(03 §5):槽位机 + 规则抽取 + 旁问处理 + 确认卡 + 出口。

状态保存在 FrontdeskCase(state=PRECHECK, intake_flow, answers_json v2),重启不丢。
- 已被抽取填充的槽位不出示;最多出示 max_questions 题;每题可跳过(skippable);
- 客户中途问别的:先答(规则)再回到当前题;连续 2 次旁问 → 直接出口;
- 旧卡片乱序点击无副作用(accept_button 返回 False)。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from telegram import InlineKeyboardMarkup

from db.database import SessionMaker
from db.model import FrontdeskCase

from ...analytics.events import emit
from ...config import cfg, copy
from ...ui import btn, kb
from .. import lines as biz_lines
from . import brief as brief_mod
from . import extract_rules as er

SKIP = "-"

LINE_ICONS = {"software": "🖥", "bot_custom": "🤖", "network": "🌐", "kyc": "🛡", "account": "📦", "ai_account": "🧠", "airdrop": "🎁", "other_biz": "💬"}
ISSUE_ICONS = {"install": "⚙️", "license": "🔑", "payment": "💳", "delivery": "📦", "renew": "🔄", "refund": "📑", "other": "💬"}
PTYPE_ICONS = {"channel": "🤝", "oem": "🏷️", "project": "💻", "supply": "📦", "other": "💬"}

# 文本匹配用的同义词(选项 code → 关键词)
ISSUE_KW = {
    "install": ["安装", "装不上", "装不了", "无法安装", "打不开", "保护你的电脑", "无法验证开发者", "使用", "install", "setup", "crash"],
    "license": ["授权", "激活", "设备", "卡密", "license", "activat", "device"],
    "payment": ["不到账", "没到账", "未到账", "付款", "paid", "payment"],
    "delivery": ["进度", "交付", "什么时候", "delivery", "progress"],
    "renew": ["续费", "升级", "renew", "upgrade"],
    "refund": ["退款", "争议", "refund", "dispute", "chargeback"],
}
PTYPE_KW = {
    "channel": ["渠道", "代理", "reseller", "agency"], "oem": ["oem", "品牌", "white label"],
    "project": ["项目", "协作", "project"], "supply": ["供应", "资源", "supply", "supplier"],
}
_YES = ("对", "是的", "没错", "确认", "可以", "好的", "好", "ok", "okay", "yes", "yep", "right", "correct", "continue", "继续")
_QWORDS = ("吗", "呢", "多少", "怎么", "如何", "多久", "什么时候", "能不能", "是否", "有没有", "几天", "how", "what", "can ", "could ", "does ", "do you", "when ", "why ")


@dataclass
class AcceptResult:
    filled: list = field(default_factory=list)
    side_reply: str | None = None
    noted: bool = False                 # side_reply 是"这个问题我记下了"类提示(非规则答复)
    ended: bool = False                 # 连续旁问,直接出口
    confirm_ok: bool = False            # 确认阶段客户用文字确认
    changed_confirm: bool = False       # 确认阶段客户补充了内容 → 需重发确认卡


@dataclass
class Completed:
    kind: str                # "ai" | "handoff"
    text: str                # 发给客户的确认/收尾卡(可为空:确认卡已收起时)
    query: str | None = None # kind == "ai" 时交给 AI 的首轮输入(WP5 前沿用预检摘要)
    reason: str = ""
    summary: str = ""
    prio: str | None = None
    collapsed: str = ""        # 问答完成后进度卡收起成的一行文字


# ---------- 配置读取 ----------
def flows() -> dict:
    return cfg.get("intake.flows", default={})


def max_questions() -> int:
    return int(cfg.get("intake.max_questions", default=3))


def bands() -> list[dict]:
    return cfg.get("intake.budget_bands", default=[])


def large_deal_u() -> float:
    return float(cfg.get("escalation.large_deal_u", default=2000))


# ---------- case/state ----------
def _case(db, user_id: int, owner_id: int = 0):
    return db.query(FrontdeskCase).filter_by(user_id=user_id, business_owner_id=owner_id).order_by(FrontdeskCase.id.desc()).first()


def new_state(flow_id: str, first: str = "", line: str | None = None) -> dict:
    return {"v": 2, "flow": flow_id, "line": line, "slots": {}, "sources": {}, "shown": [], "skipped": [], "side": 0, "first": first,
            "pending_q": [], "signals": {}, "money": None, "confirm": None, "started": datetime.now(timezone.utc).isoformat()}


def legacy_to_state(a: dict, role: str | None) -> dict:
    """旧 answers_json(line/need/extra/product/issue/desc/ptype/offer/how)→ v2 槽位(05 §4 第 6 条)。"""
    if a.get("v") == 2:
        return a
    line = a.get("line") or a.get("product")
    flow = {"prospect": biz_lines_flow(line), "support": "support", "partner": "partner", "visitor": "visitor"}.get(role or "prospect", "generic")
    st = new_state(flow, a.get("first", ""), line if role == "prospect" else None)
    mapping = {"line": "line", "need": "need", "extra": "key_feature", "product": "product", "issue": "issue", "desc": "desc", "ptype": "ptype", "offer": "scale"}
    for old, new in mapping.items():
        if a.get(old) not in (None, ""):
            st["slots"][new] = a[old] if a[old] != SKIP else ""
            if a[old] == SKIP:
                st["slots"].pop(new)
                st["skipped"].append(new)
    if a.get("how") and "scale" in st["slots"]:
        st["slots"]["scale"] += f"({a['how']})"
    elif a.get("how"):
        st["slots"]["scale"] = a["how"]
    st["done"] = a.get("done", False)
    return st


def biz_lines_flow(line: str | None) -> str:
    return cfg.get("intake.line_flow", default={}).get(line or "", "generic") if line else "prospect"


def _state(case) -> dict:
    try:
        a = json.loads(case.answers_json or "{}")
    except ValueError:
        a = {}
    return legacy_to_state(a, case.role) if a.get("v") != 2 else a


def _save(db, case, st: dict) -> None:
    st["line_top"] = st.get("line")
    top = dict(st)
    top["line"] = st.get("line") or st["slots"].get("line") or st["slots"].get("product")      # 兼容:管理台等读取顶层 line
    case.answers_json = json.dumps(top, ensure_ascii=False)
    case.intake_flow = st["flow"]
    _sync_brief(case, st)


def _sync_brief(case, st: dict) -> None:
    b = brief_mod.HandoffBrief.from_json(case.brief_json)
    b.case_id, b.channel, b.role = case.id, case.channel or "bot", case.role
    b.source = case.source or b.source
    b.line = st.get("line") or st["slots"].get("line") or st["slots"].get("product") or b.line
    b.slots = {k: v for k, v in st["slots"].items() if k not in ("line",)}
    b.slot_sources = dict(st["sources"])
    band = st["slots"].get("budget")
    m = st.get("money")
    b.budget = {"band": band if band != "declined" else None, "stated": (f"{m['amount']:g} {m['currency']}" if m else None),
                "asked": "budget" in st["shown"], "declined": band == "declined"}
    b.signals = dict(st.get("signals", {}))
    b.pending_questions = list(st.get("pending_q", []))
    b.first_message = st.get("first", "")
    b.missing = [s["key"] for s in _flow(st).get("slots", []) if s.get("required") and s["key"] not in st["slots"] and s["key"] not in st["skipped"]]
    case.brief_json = b.to_json()


def _flow(st: dict) -> dict:
    return flows().get(st["flow"]) or flows().get("generic") or {"slots": [], "exit": "ai", "role": "prospect"}


def _slot_def(flow: dict, key: str) -> dict | None:
    return next((s for s in flow.get("slots", []) if s["key"] == key), None)


def _compact(text: str) -> str:
    return "".join((text or "").split())


def looks_like_question(text: str) -> bool:
    t = (text or "").strip().lower()
    return t.endswith(("?", "？")) or "?" in t or "？" in t or any(w in t for w in _QWORDS)


# ---------- 抽取器 ----------
def _band_for(m: dict) -> str | None:
    amount = er.to_units(m, float(cfg.get("intake.cny_per_u", default=7.0)))
    for b in bands():
        if b["code"] == "declined":
            continue
        lo, hi = b["lo"], b["hi"]
        if amount >= lo and (hi is None or amount < hi):
            return b["code"]
    return None


def _ex_line(text, ex, st, slot):
    ln = biz_lines.match_line(text)
    return ln["id"] if ln and not ln.get("sensitive") else None


def _ex_need(text, ex, st, slot):
    t = (text or "").strip()
    if len(_compact(t)) < 10:
        return None
    if st.get("line") or st["slots"].get("line") or biz_lines.match_line(t):
        return t
    return None


def _ex_deadline(text, ex, st, slot):
    if ex.get("deadline"):
        return ex["deadline"]
    return "week" if ex.get("urgency") == "urgent" else None


def _ex_budget(text, ex, st, slot):
    m = ex.get("money")
    return _band_for(m) if m else None


def _ex_horizon(text, ex, st, slot):
    return ex.get("horizon")


def _ex_devices(text, ex, st, slot):
    n = ex.get("devices")
    if n is None:
        return None
    return "1" if n <= 1 else ("2-3" if n <= 3 else "4+")


def _ex_os(text, ex, st, slot):
    return ex.get("os")


def _ex_ip_type(text, ex, st, slot):
    low = text.lower()
    if re.search(r"住宅|residential", low):
        return "residential"
    if re.search(r"机房|datacenter|data center|idc", low):
        return "datacenter"
    return None


def _ex_qty_band(text, ex, st, slot):
    n = ex.get("qty")
    if n is None:
        return None
    return "1-10" if n <= 10 else ("10-100" if n <= 100 else "100+")


def _ex_qty_small(text, ex, st, slot):
    n = ex.get("qty")
    if n is None:
        return None
    return "1" if n <= 1 else ("2-5" if n <= 5 else "more")


def _ex_duration(text, ex, st, slot):
    low = text.lower()
    if re.search(r"一年|半年|长期|longer|year|6 months|半年", low):
        return "longer"
    if re.search(r"三个月|3\s*个月|3 months|three months|quarter", low):
        return "3m"
    if re.search(r"一个月|1\s*个月|1 month|a month|monthly", low):
        return "1m"
    return None


def _ex_ai_product(text, ex, st, slot):
    low = text.lower()
    if "gemini" in low:
        return "gemini"
    if "claude" in low:
        return "claude"
    if re.search(r"chatgpt|gpt", low):
        return "chatgpt_pro" if re.search(r"\bpro\b", low) else ("chatgpt_plus" if "plus" in low else None)
    return None


def _ex_issue(text, ex, st, slot):
    low = text.lower()
    for code, kws in ISSUE_KW.items():
        if any(k in low for k in kws):
            return code
    return None


def _ex_ptype(text, ex, st, slot):
    low = text.lower()
    for code, kws in PTYPE_KW.items():
        if any(k in low for k in kws):
            return code
    return None


def _ex_desc(text, ex, st, slot):
    return text.strip() if st["slots"].get("issue") and len(_compact(text)) >= 10 else None


EXTRACTORS = {
    "line": _ex_line, "need": _ex_need, "deadline": _ex_deadline, "budget": _ex_budget, "horizon": _ex_horizon,
    "devices": _ex_devices, "os": _ex_os, "ip_type": _ex_ip_type, "qty_band": _ex_qty_band, "qty_band_small": _ex_qty_small,
    "duration": _ex_duration, "ai_product": _ex_ai_product, "issue": _ex_issue, "ptype": _ex_ptype, "desc": _ex_desc,
}


def _fill(st: dict, key: str, value, source: str) -> None:
    st["slots"][key] = value
    st["sources"][key] = source
    if key in st["skipped"]:
        st["skipped"].remove(key)
    if key == "budget":
        st["budget_declined"] = value == "declined"


def _update_signals(st: dict, ex: dict) -> None:
    sig = st.setdefault("signals", {})
    for k in ("urgency", "sentiment", "trust_concern", "company", "reseller", "questions", "order_ref"):
        if k in ex:
            sig[k] = ex[k]
    if ex.get("money"):
        st["money"] = ex["money"]


def fill_from_text(st: dict, text: str, source: str = "rule") -> list[str]:
    """跑规则抽取,把能填的槽位都填上。返回新填充的槽位名。"""
    flow = _flow(st)
    ex = er.extract_all(text)
    _update_signals(st, ex)
    filled = []
    for slot in flow.get("slots", []):
        key = slot["key"]
        if key in st["slots"] or key in st["skipped"] or not _applicable(slot, st):
            continue
        for name in slot.get("extract", []):
            fn = EXTRACTORS.get(name)
            val = fn(text, ex, st, slot) if fn else None
            if val not in (None, ""):
                _fill(st, key, val, source if name != "need" else "first_message")
                filled.append(key)
                break
    return filled


# ---------- 条件 ----------
def _cond_devices_unless_multi(slot, st):
    return st["slots"].get("horizon") != "multi"


def _cond_budget(slot, st):
    if "budget" in st["slots"]:
        return False
    return not (st["slots"].get("deadline") == "week" and st.get("signals", {}).get("urgency") == "urgent")


CONDITIONS = {"devices_unless_multi": _cond_devices_unless_multi, "budget_unless_urgent_or_known": _cond_budget}


def _applicable(slot: dict, st: dict) -> bool:
    cond = slot.get("when")
    if cond and not CONDITIONS.get(cond, lambda s, t: True)(slot, st):
        return False
    return True


def _skippable(slot: dict, st: dict) -> bool:
    return bool(slot.get("skippable")) or st["slots"].get("issue") in (slot.get("skippable_when_issue") or [])


def _next_slot(st: dict) -> dict | None:
    if st.get("force_complete"):
        return None
    flow = _flow(st)
    force = st.get("force_slot")
    if force:
        s = _slot_def(flow, force)
        if s and force not in st["slots"] and force not in st["skipped"]:
            return s
        st.pop("force_slot", None)
    cap = max_questions()
    for slot in flow.get("slots", []):
        key = slot["key"]
        if key in st["slots"] or key in st["skipped"] or not _applicable(slot, st):
            continue
        if key in st["shown"]:
            return slot
        if len(st["shown"]) >= cap:
            continue
        return slot
    return None


# ---------- 对外 API ----------
def start_flow(user_id: int, flow_id: str, first_text: str = "", *, owner_id: int = 0, channel: str = "bot", line: str | None = None) -> None:
    flow = flows().get(flow_id) or flows()["generic"]
    st = new_state(flow_id, first_text, line or flow.get("line"))
    if line or flow.get("line"):
        st["slots"]["line"] = line or flow["line"]
    if first_text:
        fill_from_text(st, first_text, "rule")
    with SessionMaker() as db:
        c = _case(db, user_id, owner_id)
        if not c:
            c = FrontdeskCase(user_id=user_id, business_owner_id=owner_id, channel=channel, initial_message=first_text)
            db.add(c)
            db.flush()
        if not c.initial_message:
            c.initial_message = first_text
        c.role, c.state = flow.get("role", "prospect"), "PRECHECK"
        c.last_customer_at = datetime.now(timezone.utc)
        _save(db, c, st)
        db.commit()
    emit("intake_started", user_id, flow=flow_id[:32])


def active_state(user_id: int, owner_id: int = 0) -> tuple[dict, dict] | None:
    """(case_dict, state);非问答中返回 None。"""
    with SessionMaker() as db:
        c = _case(db, user_id, owner_id)
        if not c or c.state != "PRECHECK":
            return None
        return {"id": c.id, "role": c.role}, _state(c)


def _mutate(user_id: int, owner_id: int, fn):
    """在事务内读-改-写状态;fn(st, case) 返回任意值。非问答中返回 None。"""
    with SessionMaker() as db:
        c = _case(db, user_id, owner_id)
        if not c or c.state != "PRECHECK":
            return None
        st = _state(c)
        res = fn(st, c)
        _save(db, c, st)
        db.commit()
        return res


def is_active(user_id: int, owner_id: int = 0) -> bool:
    return active_state(user_id, owner_id) is not None


def current_step(user_id: int, owner_id: int = 0) -> str | None:
    a = active_state(user_id, owner_id)
    if not a:
        return None
    s = _next_slot(a[1])
    return s["key"] if s else None


def _options(slot: dict, lang: str) -> list[tuple[str, str]]:
    dyn = slot.get("dynamic")
    if dyn in ("lines", "lines_with_other"):
        out = []
        for ln in biz_lines.get_lines():
            if ln.get("sensitive"):
                continue
            name = ln.get("name_en" if lang == "en" else "name_zh") or ln["id"]
            icon = LINE_ICONS.get(ln["id"], "•")
            out.append((ln["id"], name if name.startswith(icon) else f"{icon} {name}"))
        out.append(("other", "💬 " + ("其他 / 还没想好" if lang == "zh" else "Other / not sure")))
        return out
    if slot["kind"] == "band":
        return [(b["code"], b.get(lang) or b["zh"]) for b in bands()]
    icons = {"issue": ISSUE_ICONS, "ptype": PTYPE_ICONS}.get(slot["key"], {})
    out = []
    for o in slot.get("options", []):
        name = o.get(lang) or o["zh"]
        ic = icons.get(o["code"])
        out.append((o["code"], f"{ic} {name}" if ic else name))
    return out


def _progress(st: dict, slot: dict) -> str:
    cap = max_questions()
    shown = list(st["shown"])
    if slot["key"] not in shown:
        shown.append(slot["key"])
    flow = _flow(st)
    pending = [s for s in flow.get("slots", []) if s["key"] not in st["slots"] and s["key"] not in st["skipped"]
               and s["key"] not in shown and _applicable(s, st)]
    total = min(cap, len(shown) + len(pending))
    if total <= 1:
        return ""
    done = sum(1 for k in shown if k in st["slots"] or k in st["skipped"])
    return " ".join("●" if i < done else ("◉" if i == done else "○") for i in range(total))


def current_question(user_id: int, lang: str, owner_id: int = 0) -> tuple[str, InlineKeyboardMarkup | None] | None:
    lang = lang if lang in ("zh", "en") else "zh"

    def run(st, c):
        slot = _next_slot(st)
        if not slot:
            return None
        if slot["key"] not in st["shown"]:
            st["shown"].append(slot["key"])
        ask = (slot["ask"].get(lang) or slot["ask"]["zh"])
        if slot["key"] == "desc" and st["slots"].get("issue") == "license":
            ask = re.sub(r"<[^>]+>|\[\[\w+\]\]", "", copy("support.license.ask", lang)).split("\n")[0].strip()
        if st.pop("resume_prefix", False):
            ask = copy("intake.resume_prefix", lang) + ask
        hint = (slot.get("hint") or {}).get(lang, "")
        icon = "[[package]]" if slot["key"] in ("line", "product") else ("[[question]]" if slot["kind"] != "text" else "[[note]]")
        text = f"{icon} <b>{ask}</b>" + (f"\n{hint}" if hint else "")
        prog = _progress(st, slot)
        if prog:
            text += f"\n\n{prog}"
        uid = user_id
        rows = []
        if slot["kind"] in ("choice", "band"):
            opts = _options(slot, lang)
            two_col = slot["key"] in ("issue", "ptype")
            btns = [btn(label, style="primary", callback_data=f"fd:ans:{uid}:{slot['key']}:{code}") for code, label in opts]
            rows = [btns[i:i + 2] for i in range(0, len(btns), 2)] if two_col else [[b] for b in btns]
        if _skippable(slot, st):
            rows.append([btn(("⏭️ " + copy("intake.skip", lang)), callback_data=f"fd:skip:{uid}:{slot['key']}")])
        return text, (kb(rows) if rows else None)
    return _mutate(user_id, owner_id, run)


def _switch_line(st: dict, line_id: str) -> None:
    """业务线选定后切换到该线的流程,保留已填槽位并重新预填。"""
    new_flow = cfg.get("intake.line_flow", default={}).get(line_id) or ("generic" if line_id == "other" else "generic")
    if line_id == "other":
        line_id = "other_biz"
        new_flow = "other_biz"
    st["flow"], st["line"], st["shown"] = new_flow, line_id, []
    st["slots"]["line"] = line_id
    if st.get("first"):
        fill_from_text(st, st["first"], "first_message")


def _set_choice(st: dict, slot: dict, code: str, source: str) -> bool:
    valid = {c for c, _ in _options(slot, "zh")}
    if code not in valid:
        return False
    if slot["key"] == "line":
        st["slots"]["line"] = code
        st["sources"]["line"] = source
        _switch_line(st, code)
        return True
    _fill(st, slot["key"], code, source)
    return True


def accept_button(user_id: int, slot_key: str, code: str, owner_id: int = 0) -> bool:
    """按钮作答。步骤不在当前问答中,或已作答,则忽略(旧卡片乱序点击无副作用)。"""
    def run(st, c):
        flow = _flow(st)
        slot = _slot_def(flow, slot_key)
        if not slot or slot_key in st["slots"] or slot_key in st["skipped"]:
            return False
        if slot_key not in st["shown"] and (_next_slot(st) or {}).get("key") != slot_key:
            return False
        if slot["kind"] == "text":
            return False
        ok = _set_choice(st, slot, code, "button")
        if ok:
            st["side"] = 0
        return ok
    ok = _mutate(user_id, owner_id, run)
    if ok:
        emit("intake_answered", user_id, slot=slot_key[:32], method="button")
    return bool(ok)


def skip(user_id: int, slot_key: str, owner_id: int = 0) -> bool:
    def run(st, c):
        slot = _slot_def(_flow(st), slot_key)
        if not slot or not _skippable(slot, st) or slot_key in st["slots"] or slot_key in st["skipped"]:
            return False
        st["skipped"].append(slot_key)
        st["side"] = 0
        return True
    ok = _mutate(user_id, owner_id, run)
    if ok:
        emit("intake_skipped", user_id, slot=slot_key[:32])
    return bool(ok)


def _match_option(slot: dict, text: str) -> str | None:
    low = (text or "").strip().lower()
    if not low:
        return None
    if slot["kind"] == "band":
        m = er.money(text)
        return _band_for(m) if m else (next((b["code"] for b in bands() if low in (b["zh"].lower(), b.get("en", "").lower())), None)
                                       or ("declined" if re.search(r"不说|不方便|rather not|skip|不透露", low) else None))
    for code, label in _options(slot, "zh") + _options(slot, "en"):
        plain = re.sub(r"^[^\w一-鿿]+", "", label).strip().lower()
        if low == plain or low == code.lower() or (len(low) >= 2 and low in plain):
            return code
    return None


def side_answer(text: str, line: str | None, lang: str) -> str | None:
    low = (text or "").lower()
    for rule in cfg.get("intake.side_answers", default=[]):
        if rule.get("lines") and line not in rule["lines"]:
            continue
        if any(k.lower() in low for k in rule["kw"]):
            return rule.get(lang) or rule["zh"]
    return None


def accept_text(user_id: int, text: str, lang: str = "zh", owner_id: int = 0) -> AcceptResult:
    """客户在问答中直接打字(03 §5.3):抽取 → 判断是否在回答当前题 → 否则作旁问处理。"""
    text = (text or "").strip()
    res = AcceptResult()

    def run(st, c):
        flow = _flow(st)
        if st.get("confirm") == "shown":
            return _confirm_text(st, text, res)
        cur = _next_slot(st)
        before = set(st["slots"])
        filled = fill_from_text(st, text, "rule")
        res.filled = filled
        if cur is None:
            return res
        if cur["key"] in filled:
            st["side"] = 0
            return res
        if cur["kind"] in ("choice", "band"):
            code = _match_option(cur, text)
            if code and _set_choice(st, cur, code, "text"):
                res.filled.append(cur["key"])
                st["side"] = 0
                return res
        elif cur.get("anytext") or (len(text) >= 2 and not looks_like_question(text)):
            _fill(st, cur["key"], text[:300], "text")
            res.filled.append(cur["key"])
            st["side"] = 0
            return res
        # 选择题下客户给了一段陈述(非问句):存进第一个空的文本槽位,当前题稍后重出
        if cur["kind"] in ("choice", "band") and not looks_like_question(text) and len(_compact(text)) >= 6:
            for s in flow.get("slots", []):
                if s["kind"] == "text" and s["key"] not in st["slots"] and s["key"] not in st["skipped"]:
                    _fill(st, s["key"], text[:300], "text")
                    res.filled.append(s["key"])
                    return res
        if filled:
            return res
        # 旁问
        st["side"] = st.get("side", 0) + 1
        reply = side_answer(text, st.get("line") or st["slots"].get("line"), lang)
        if reply:
            res.side_reply = reply
        else:
            res.side_reply, res.noted = copy("intake.side_noted", lang), True
            if len(st["pending_q"]) < 5:
                st["pending_q"].append(text[:200])
        st["resume_prefix"] = True
        if st["side"] >= 2:
            st["force_complete"] = True
            res.ended = True
        return res
    out = _mutate(user_id, owner_id, run)
    if out is None:
        return res
    for k in res.filled:
        emit("intake_answered", user_id, slot=k[:32], method="text")
    if res.side_reply:
        emit("intake_side_question", user_id, slot=(current_step(user_id, owner_id) or "-")[:32])
    return res


def image_note(user_id: int, owner_id: int = 0) -> bool:
    """问答中收到图片:提示一次(每 case 一次)。"""
    def run(st, c):
        if st.get("image_noted"):
            return False
        st["image_noted"] = True
        return True
    return bool(_mutate(user_id, owner_id, run))


# ---------- 确认卡 ----------
def _value_text(slot: dict | None, code, lang: str) -> str:
    if not slot:
        return str(code)
    if slot["kind"] == "band":
        b = next((x for x in bands() if x["code"] == code), None)
        return (b.get(lang) or b["zh"]) if b else str(code)
    for o in slot.get("options", []):
        if o["code"] == code:
            return o.get(lang) or o["zh"]
    if slot.get("dynamic"):
        ln = next((x for x in biz_lines.get_lines() if x["id"] == code), None)
        if ln:
            return ln.get("name_en" if lang == "en" else "name_zh") or code
    return str(code)


def summary_lines(st: dict, lang: str, include_line: bool = True) -> str:
    flow = _flow(st)
    out = []
    for slot in flow.get("slots", []):
        key = slot["key"]
        if key not in st["slots"] or key == "line" and flow.get("line"):
            continue
        label = (slot.get("label") or {}).get(lang) or key
        out.append(f"• {label}：{_value_text(slot, st['slots'][key], lang)}" if lang == "zh" else f"• {label}: {_value_text(slot, st['slots'][key], lang)}")
    line = st.get("line")
    if include_line and line and flow.get("line"):
        ln = next((x for x in biz_lines.get_lines() if x["id"] == line), None)
        name = (ln.get("name_en" if lang == "en" else "name_zh") if ln else line) or line
        out.insert(0, f"• {'业务' if lang == 'zh' else 'Service'}{'：' if lang == 'zh' else ': '}{name}")
    return "\n".join(out)


def _is_large(st: dict) -> bool:
    m = st.get("money")
    amount_u = er.to_units(m, float(cfg.get("intake.cny_per_u", default=7.0))) if m else 0
    return st["slots"].get("budget") == "2000+" or amount_u >= large_deal_u()


def _needs_confirm(st: dict) -> bool:
    flow = _flow(st)
    if flow.get("role") == "prospect" and _is_large(st):
        return False                                                  # 大单直接交给同事,不再出确认卡
    if not flow.get("confirm") or st.get("confirm") in ("ok", "shown"):
        return False
    if st.get("force_complete"):
        return False
    return len([k for k in st["slots"] if k != "line"]) >= 2


def pending_notice(user_id: int, lang: str, owner_id: int = 0) -> str | None:
    """确认卡前的一次性提示(预算 300U 以内的轻量方案说明)。"""
    def run(st, c):
        if st["slots"].get("budget") == "<300" and not st.get("low_budget_sent") and _needs_confirm(st):
            st["low_budget_sent"] = True
            return copy("card.custom_low_budget", lang)
        return None
    return _mutate(user_id, owner_id, run)


def confirm_card(user_id: int, lang: str, owner_id: int = 0) -> tuple[str, InlineKeyboardMarkup] | None:
    def run(st, c):
        if _next_slot(st) is not None or not _needs_confirm(st):
            return None
        st["confirm"] = "shown"
        text = copy("intake.progress_confirm", lang, lines=summary_lines(st, lang, include_line=False))
        markup = kb([[btn(copy("intake.btn_ok", lang), style="success", callback_data=f"fd:ok:{user_id}")],
                     [btn(copy("intake.btn_fix", lang), callback_data=f"fd:fix:{user_id}")]])
        return text, markup
    return _mutate(user_id, owner_id, run)


def _short(text: str, n: int = 24) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def collapsed_text(st: dict, lang: str) -> str:
    ln = next((x for x in biz_lines.get_lines() if x["id"] == st.get("line")), None)
    name = (ln.get("name_en" if lang == "en" else "name_zh") if ln else None) or (st.get("line") or "")
    need = st["slots"].get("need") or st["slots"].get("key_feature") or st["slots"].get("message") or ""
    return copy("intake.collapsed", lang, line_name=name, need_short=_short(need))


def confirm_ok(user_id: int, lang: str, owner_id: int = 0) -> str | None:
    """确认卡"对,继续":返回收起后的卡片文案;不在确认阶段返回 None。"""
    def run(st, c):
        if st.get("confirm") != "shown":
            return None
        st["confirm"] = "ok"
        return collapsed_text(st, lang)
    return _mutate(user_id, owner_id, run)


def fix_list(user_id: int, lang: str, owner_id: int = 0) -> tuple[str, InlineKeyboardMarkup] | None:
    def run(st, c):
        if st.get("confirm") != "shown":
            return None
        flow = _flow(st)
        rows = []
        for slot in flow.get("slots", []):
            if slot["key"] in st["slots"] and slot["key"] != "line":
                label = (slot.get("label") or {}).get(lang) or slot["key"]
                rows.append([btn(f"✏️ {label}", callback_data=f"fd:fixslot:{user_id}:{slot['key']}")])
        return ("[[edit]] <b>" + ("改哪一项?" if lang == "zh" else "Which one to change?") + "</b>"), kb(rows)
    return _mutate(user_id, owner_id, run)


def fix_slot(user_id: int, slot_key: str, owner_id: int = 0) -> bool:
    def run(st, c):
        if st.get("confirm") != "shown" or slot_key not in st["slots"]:
            return False
        st["slots"].pop(slot_key, None)
        st["sources"].pop(slot_key, None)
        st["force_slot"] = slot_key
        st["confirm"] = None
        if slot_key not in st["shown"]:
            st["shown"].append(slot_key)
        return True
    ok = _mutate(user_id, owner_id, run)
    if ok:
        emit("intake_corrected", user_id, slot=slot_key[:32])
    return bool(ok)


def _confirm_text(st: dict, text: str, res: AcceptResult):
    if text.strip().lower().strip("!！。. ") in _YES:
        st["confirm"] = "ok"
        res.confirm_ok = True
        return res
    filled = fill_from_text(st, text, "rule")
    if not filled and _slot_def(_flow(st), "need") and st["slots"].get("need"):
        st["slots"]["need"] = (st["slots"]["need"] + "；" + text)[:300]
    elif not filled and st["slots"].get("key_feature"):
        st["slots"]["key_feature"] = (st["slots"]["key_feature"] + "；" + text)[:300]
    res.filled = filled
    st["confirm"] = None
    res.changed_confirm = True
    return res


# ---------- 出口 ----------
def _eta(prio: str, lang: str) -> str:
    from .. import escalation
    try:
        return escalation.eta_text(prio, lang)
    except Exception:
        return ""


def _done_texts() -> dict:
    return {
        "zh": {"done_escalate": "[[check]] <b>已登记，正在安排专人跟进</b>\n{lines}", "done_sensitive": "[[check]] 已登记，同事评估后会在这里回复您。", "done_visitor": "[[check]] <b>留言已收到</b>\n同事查看后会在这里回复您。", "done_support": "[[check]] <b>问题已登记</b>\n{lines}", "done_prospect": "[[check]] <b>需求已整理</b>\n{lines}", "done_partner": "[[check]] <b>合作意向已收录</b>\n{lines}"},
        "en": {"done_escalate": "[[check]] <b>Logged; a teammate will follow up.</b>\n{lines}", "done_sensitive": "[[check]] Logged; a teammate will review this here.", "done_visitor": "[[check]] <b>Message received</b>\nA teammate will reply here.", "done_support": "[[check]] <b>Issue logged</b>\n{lines}", "done_prospect": "[[check]] <b>Needs captured</b>\n{lines}", "done_partner": "[[check]] <b>Partnership interest logged</b>\n{lines}"},
    }


def _line_label(st: dict, lang: str) -> str:
    return summary_lines(st, lang)


def complete(user_id: int, lang: str, owner_id: int = 0) -> Completed | None:
    """问答收齐:落库新状态并生成收尾卡与交接信息。未收齐返回 None。"""
    lang = lang if lang in ("zh", "en") else "zh"
    holder: dict = {}

    def run(st, c):
        if _next_slot(st) is not None:
            return None
        if _needs_confirm(st):
            return None
        flow = _flow(st)
        P = _done_texts()[lang]
        exit_ = flow.get("exit", "ai")
        role = c.role or flow.get("role", "prospect")
        lines_txt = summary_lines(st, lang)
        lines_zh = summary_lines(st, "zh")
        first = st.get("first") or c.initial_message or ""
        confirmed = st.get("confirm") == "ok"
        prio = flow.get("prio")
        if role == "prospect" and _is_large(st):
            kind, state, text, reason, prio = "handoff", "HUMAN_PENDING", copy("handoff.large_deal", lang, eta=_eta("P1", lang)).strip(), "large_deal", "P1"
        elif role == "support" and st["slots"].get("issue") in ("payment", "refund"):
            issue = st["slots"]["issue"]
            kind, state, text, reason, prio = "handoff", "HUMAN_PENDING", P["done_escalate"], f"support_escalation:{issue}", "P0"
        elif role == "support" and st["slots"].get("issue") == "license":
            kind, state, text, reason, prio = "handoff", "HUMAN_PENDING", copy("handoff.paid_but_broken", lang, eta=_eta("P0", lang)).strip(), "paid_but_broken", "P0"
        elif exit_.startswith("handoff:"):
            reason = exit_.split(":", 1)[1]
            kind, state = "handoff", "HUMAN_PENDING"
            if reason == "quote_request":
                prio = "P1" if st["slots"].get("qty") == "100+" else flow.get("prio", "P2")
                text = copy("handoff.quote_request", lang, eta=_eta(prio, lang)).strip()
            elif reason == "partner_intake":
                text = copy("handoff.partner_intake", lang, eta=_eta(prio or "P1", lang)).strip()
            elif reason == "sensitive_line":
                text = P["done_sensitive"]
            else:
                text = P["done_visitor"]
        elif role == "support":
            kind, state, text, reason = "ai", "AI_SUPPORT", P["done_support"], ""
        else:
            kind, state, text, reason = "ai", "AI_SALES", ("" if confirmed else P["done_prospect"]), ""
        c.state = state
        st["done"] = True
        c.summary = (f"[{role}] " + lines_zh.replace("\n", " ") + (f" | 原话:{first[:80]}" if first else ""))[:900]
        asked = len([k for k in st["shown"] if k in st["slots"] or k in st["skipped"]])
        secs = int((datetime.utcnow() - c.created_at.replace(tzinfo=None)).total_seconds()) if c.created_at else 0
        holder.update(role=role, asked=asked, secs=max(secs, 0), flow=st["flow"])
        card = text.format(lines=lines_txt) if "{lines}" in text else text
        query = (first.strip() or copy("intake.marker_buttons", lang)) if kind == "ai" else None
        return Completed(kind, card, query, reason, c.summary, prio, collapsed_text(st, lang))
    done = _mutate(user_id, owner_id, run)
    if done:
        emit("intake_completed", user_id, flow=holder["flow"][:32], asked=holder["asked"], secs=holder["secs"])
    return done
