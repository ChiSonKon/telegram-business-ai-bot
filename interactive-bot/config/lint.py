"""配置校验(04 §3.4)。error 阻止发布;warn 需二次确认。目的:让"改坏了让机器人沉默"在发布前就被拦下。"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import registry as R

ALLOWED_HTML = {"b", "i", "u", "s", "code", "pre", "a", "tg-emoji", "blockquote"}
PRECHECK_FORBIDDEN = ("人工", "安全验证", "安全预检", "security verified", "verify")
CLAIMS = ("保证", "包过", "稳赚", "无限", "100%", "guarantee", "unlimited")
ROLES = ("owner", "manager", "agent", "finance", "viewer")


@dataclass
class Issue:
    level: str          # error | warn
    msg: str

    def __str__(self) -> str:
        return ("❌ " if self.level == "error" else "⚠️ ") + self.msg


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error"]


def _err(out, msg):
    out.append(Issue("error", msg))


def _warn(out, msg):
    out.append(Issue("warn", msg))


# ---------- copy ----------
_TAG = re.compile(r"</?([A-Za-z][A-Za-z0-9\-]*)([^>]*)>")


def _check_html(text: str, label: str, out: list):
    stack = []
    for m in _TAG.finditer(re.sub(r"\[\[\w+\]\]", "", text)):
        tag = m.group(1).lower()
        if tag not in ALLOWED_HTML:
            _err(out, f"{label}:不允许的 HTML 标签 <{tag}>(仅支持 b i u s code pre a tg-emoji blockquote)")
            continue
        if m.group(0).startswith("</"):
            if not stack or stack[-1] != tag:
                _err(out, f"{label}:标签 </{tag}> 没有对应的开始标签或嵌套顺序错误")
            else:
                stack.pop()
        elif not m.group(0).endswith("/>"):
            stack.append(tag)
    if stack:
        _err(out, f"{label}:标签 <{stack[-1]}> 没有闭合")


def _lint_copy(ck: R.ConfigKey, value, out: list):
    if not isinstance(value, dict) or not isinstance(value.get("zh"), str) or not isinstance(value.get("en"), str):
        _err(out, "文案必须同时提供中文(zh)和英文(en)两个字符串")
        return
    want = {lang: ck.placeholders(lang) for lang in ("zh", "en")}
    ref = want["zh"] | want["en"]
    for lang in ("zh", "en"):
        text = value[lang]
        if not text.strip():
            _err(out, f"{lang} 版本不能为空")
            continue
        got = R.placeholders_of(text)
        if got != ref:
            missing, extra = sorted(ref - got), sorted(got - ref)
            _err(out, f"{lang} 占位符不一致:" + (f"缺少 {{{'}, {'.join(missing)}}} " if missing else "") + (f"多出 {{{'}, {'.join(extra)}}}" if extra else "")
                 + "(占位符不一致会导致机器人发不出这张卡)")
        try:
            text.format(**{n: "x" for n in got})
        except (KeyError, IndexError, ValueError) as exc:
            _err(out, f"{lang} 格式错误({type(exc).__name__}):花括号需成对,字面花括号请写成 {{{{ }}}}")
        if len(text) > 4096:
            _err(out, f"{lang} 超过 Telegram 单条上限 4096 字符")
        elif len(text) > 700 and "max_len:700" in ck.lint:
            _warn(out, f"{lang} 有 {len(text)} 字,超过卡片建议上限 700")
        _check_html(text, lang, out)
        if "forbid_precheck_words" in ck.lint:
            low = text.lower()
            for w in PRECHECK_FORBIDDEN:
                if w.lower() in low:
                    _err(out, f"{lang} 含禁用词「{w}」(问答/预检阶段不得出现)")
        if "forbid_claims" in ck.lint:
            low = text.lower()
            hit = [w for w in CLAIMS if w.lower() in low]
            if hit:
                _warn(out, f"{lang} 含绝对化承诺词 {hit},发布前需二次确认")


# ---------- json schemas ----------
def _lint_lines(v, out):
    if not isinstance(v, list) or not v:
        return _err(out, "业务线必须是非空数组")
    seen = set()
    for ln in v:
        if not isinstance(ln, dict) or not ln.get("id") or not (ln.get("name_zh") or ln.get("name_en")):
            _err(out, "每条业务线需要 id 与名称")
            continue
        if ln["id"] in seen:
            _err(out, f"重复的业务线 id:{ln['id']}")
        seen.add(ln["id"])
        if not isinstance(ln.get("keywords", []), list) or not isinstance(ln.get("fields", []), list):
            _err(out, f"{ln['id']}:keywords/fields 必须是数组")
        notes = (ln.get("notes_zh") or "") + (ln.get("notes_en") or "")
        if any(w.lower() in notes.lower() for w in CLAIMS):
            _warn(out, f"{ln['id']}:说明含绝对化承诺词(保证/包过/无限…),AI 可能因此向客户承诺")
        if ln.get("sensitive") and not ln.get("notes_zh"):
            _warn(out, f"{ln['id']}:敏感线建议写明只登记不承诺")


def _lint_flows(v, out):
    if not isinstance(v, dict) or not v:
        return _err(out, "流程必须是非空对象")
    reasons = _known_reasons()
    for fid, flow in v.items():
        if not isinstance(flow, dict) or not isinstance(flow.get("slots"), list):
            _err(out, f"流程 {fid}:缺少 slots 数组")
            continue
        exit_ = flow.get("exit", "ai")
        if not (exit_ == "ai" or (isinstance(exit_, str) and exit_.startswith("handoff:") and exit_.split(":", 1)[1] in reasons)):
            _err(out, f"流程 {fid}:出口 {exit_!r} 无效(应为 ai 或 handoff:<已知原因>)")
        keys = set()
        for s in flow["slots"]:
            k = s.get("key")
            if not k or k in keys:
                _err(out, f"流程 {fid}:槽位 key 缺失或重复({k})")
                continue
            keys.add(k)
            if s.get("kind") not in ("choice", "text", "band"):
                _err(out, f"流程 {fid}.{k}:kind 必须是 choice/text/band")
            if not isinstance(s.get("ask"), dict) or not s["ask"].get("zh") or not s["ask"].get("en"):
                _err(out, f"流程 {fid}.{k}:ask 需要中英文")
            if s.get("kind") == "choice" and not s.get("dynamic") and len(s.get("options") or []) < 2:
                _err(out, f"流程 {fid}.{k}:选择题至少 2 个选项")
            if s.get("kind") == "choice":
                codes = [o.get("code") for o in s.get("options") or []]
                if len(codes) != len(set(codes)):
                    _err(out, f"流程 {fid}.{k}:选项 code 重复")
            for lang in ("zh", "en"):
                for field in ("ask", "hint"):
                    txt = (s.get(field) or {}).get(lang, "")
                    if any(w.lower() in txt.lower() for w in PRECHECK_FORBIDDEN):
                        _err(out, f"流程 {fid}.{k}.{field}.{lang}:含禁用词(问答阶段不得出现「人工/安全验证/verify」)")
                    _check_html(txt, f"{fid}.{k}.{field}.{lang}", out) if txt else None
        if flow["slots"] and not any(s.get("required") for s in flow["slots"]) and exit_ != "ai":
            _warn(out, f"流程 {fid}:交接类出口没有必填槽位,同事可能收到空简报")
        # 可达性:用样例回答模拟走完,必须到达出口(这里按槽位顺序答完,看是否出现无法作答的题)
        for s in flow["slots"]:
            if s.get("kind") == "choice" and not s.get("dynamic") and not s.get("options"):
                _err(out, f"流程 {fid}.{s.get('key')}:无选项,客户无法作答")


def _lint_bands(v, out):
    if not isinstance(v, list) or not v:
        return _err(out, "预算区间必须是非空数组")
    if not any(b.get("code") == "declined" for b in v):
        _err(out, "预算题必须包含「先不说」选项(code=declined)")
    prev = None
    for b in v:
        if b.get("code") == "declined":
            continue
        if not isinstance(b.get("lo"), (int, float)) or not (b.get("hi") is None or isinstance(b.get("hi"), (int, float))):
            _err(out, f"区间 {b.get('code')}:lo/hi 必须是数字")
            continue
        if prev is not None and b["lo"] < prev:
            _err(out, "区间必须按金额升序排列且不重叠")
        prev = b["hi"] if b["hi"] is not None else float("inf")


def _known_reasons() -> set[str]:
    from .. import outbox
    from . import defaults_escalation
    return set(outbox.REASONS) | {r["reason"] for r in defaults_escalation.ESCALATION_RULES} | {"quote_request", "partner_intake", "sensitive_line", "visitor_message", "ai_stuck", "large_deal"}


def _lint_playbooks(v, out):
    if not isinstance(v, dict):
        return _err(out, "剧本必须是对象 {业务线: 剧本}")
    extra_slots = {"customer_channel", "scale", "use_case"}
    flows = (R.get_key("intake.flows").default or {})
    for line, pb in v.items():
        if not isinstance(pb, dict) or not isinstance(pb.get("stages"), list) or not pb["stages"]:
            _err(out, f"剧本 {line}:缺少 stages")
            continue
        known = {s["key"] for s in (flows.get(line) or {}).get("slots", [])} | extra_slots
        for st in pb["stages"]:
            for slot in st.get("exit_when", []) or []:
                if slot not in known:
                    _warn(out, f"剧本 {line}.{st.get('id')}:exit_when 引用的槽位 {slot} 不在该业务线流程里")
            act = st.get("action")
            if act and not (act.startswith("handoff:") or act.startswith("cta:")):
                _err(out, f"剧本 {line}.{st.get('id')}:action 必须是 handoff:<原因> 或 cta:<卡片>")
            if act and act.startswith("handoff:") and act.split(":", 1)[1] not in _known_reasons():
                _err(out, f"剧本 {line}.{st.get('id')}:未知的转交原因 {act}")
        mn = pb.get("must_not") or {}
        if not (mn.get("zh") or mn.get("en")):
            _warn(out, f"剧本 {line}:没有 must_not(禁说清单),AI 可能越界")


def _lint_escalation(v, out):
    if not isinstance(v, list):
        return _err(out, "升级规则覆盖必须是数组")
    from . import defaults_escalation
    ids = {r["id"] for r in defaults_escalation.ESCALATION_RULES}
    for r in v:
        if not isinstance(r, dict) or r.get("id") not in ids:
            _err(out, f"未知的规则 id:{r.get('id') if isinstance(r, dict) else r}")
        elif r.get("prio") and r["prio"] not in ("P0", "P1", "P2", "P3"):
            _err(out, f"{r['id']}:prio 必须是 P0–P3")
        elif r.get("enabled") is False and r["id"] in ("payment_issue", "refund", "paid_but_broken"):
            _warn(out, f"{r['id']} 是锁定规则,禁用不会生效")


def _lint_catalog(v, out):
    if not isinstance(v, dict):
        return _err(out, "商品目录必须是对象")
    for sku, item in v.items():
        if not isinstance(item, dict) or not (item.get("stars") or item.get("ton") or item.get("usdt")):
            _err(out, f"{sku}:至少需要 stars / ton / usdt 价格之一")
        elif item.get("stars") is not None and (not isinstance(item["stars"], int) or item["stars"] <= 0):
            _err(out, f"{sku}:stars 必须是正整数")
        if isinstance(item, dict) and not (item.get("title_zh") or item.get("title_en")):
            _err(out, f"{sku}:缺少标题")


def _lint_accounts(v, out):
    if not isinstance(v, list):
        return _err(out, "官方账号必须是数组")
    for a in v:
        u = (a or {}).get("username", "") if isinstance(a, dict) else ""
        if not re.fullmatch(r"[A-Za-z0-9_]{4,32}|<BOT_USERNAME>", u):
            _err(out, f"用户名格式不对:{u!r}(4–32 位字母数字下划线,不含 @)")


def _lint_addresses(v, out):
    if not isinstance(v, list):
        return _err(out, "官方地址必须是数组")
    for a in v:
        if not isinstance(a, dict) or not a.get("address") or not a.get("network"):
            _err(out, "每个地址需要 network 与 address")


def _lint_roles(v, out):
    if not isinstance(v, dict):
        return _err(out, "角色表必须是对象 {user_id: role}")
    for uid, role in v.items():
        if not str(uid).lstrip("-").isdigit():
            _err(out, f"用户 ID 必须是数字:{uid}")
        if role not in ROLES:
            _err(out, f"未知角色 {role!r}(可选 {'/'.join(ROLES)})")
    if not any(r == "owner" for r in v.values()):
        _err(out, "至少保留一位 owner,否则没有人能再修改配置")


def _lint_tolerance(v, out):
    if not isinstance(v, dict):
        return _err(out, "付款容差必须是对象 {'abs_u': '...', 'pct': '...'}")
    for f in ("abs_u", "pct"):
        val = v.get(f)
        try:
            if val is None or float(val) < 0:
                raise ValueError()
        except (ValueError, TypeError):
            _err(out, f"{f} 必须是非负数字")


def _lint_known_issues(v, out):
    if not isinstance(v, list):
        return _err(out, "已知问题规则必须是数组")
    for idx, item in enumerate(v):
        if not isinstance(item, dict) or not item.get("id") or not item.get("card_key"):
            _err(out, f"第 {idx+1} 条规则缺少 id 或 card_key")


_SCHEMAS = {"lines": _lint_lines, "flows": _lint_flows, "bands": _lint_bands, "playbooks": _lint_playbooks, "escalation": _lint_escalation,
            "catalog": _lint_catalog, "accounts": _lint_accounts, "addresses": _lint_addresses, "roles": _lint_roles,
            "tolerance": _lint_tolerance, "known_issues": _lint_known_issues}



def lint(key: str, value) -> list[Issue]:
    out: list[Issue] = []
    ck = R.get_key(key)
    if ck is None:
        _err(out, f"{key} 不是可编辑的配置项" + ("(锁定项,只能改代码)" if key in R.LOCKED_KEYS else ""))
        return out
    if ck.kind == "copy":
        _lint_copy(ck, value, out)
        return out
    if ck.kind == "bool" and not isinstance(value, bool):
        _err(out, "必须是 true / false")
    for rule in ck.lint:
        kind, _, arg = rule.partition(":")
        if kind == "schema":
            fn = _SCHEMAS.get(arg)
            if fn:
                fn(value, out)
            elif arg == "dict" and not isinstance(value, dict):
                _err(out, "必须是 JSON 对象")
            elif arg == "list" and not isinstance(value, list):
                _err(out, "必须是 JSON 数组")
        elif kind in ("int", "num"):
            lo, hi = (float(x) for x in arg.split(":"))
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind == "int" and int(value) != value):
                _err(out, "必须是" + ("整数" if kind == "int" else "数字"))
            elif not lo <= value <= hi:
                _err(out, f"取值范围 {lo:g}–{hi:g}")
        elif kind == "max_len" and isinstance(value, str) and len(value) > int(arg):
            _err(out, f"长度不能超过 {arg}")
        elif kind == "forbid_claims" and isinstance(value, str):
            hit = [w for w in CLAIMS if w.lower() in value.lower()]
            if hit:
                _warn(out, f"含绝对化承诺词 {hit},发布前需二次确认")
    return out
