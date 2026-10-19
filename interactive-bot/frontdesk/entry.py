"""入口分类器(03 §3.1):规则、零模型,按顺序命中即停。

对新 case 的首条消息,以及 FRONTDESK/ANSWERED 状态下的消息运行。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import cfg
from . import lines as biz_lines

_HANDLE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{3,31})\b")
_ADDR = re.compile(r"\b(?:EQ|UQ|kQ|0Q)[A-Za-z0-9_\-]{46}\b|\b0x[a-fA-F0-9]{40}\b|\bT[1-9A-HJ-NP-Za-km-z]{33}\b|\bT[A-Za-z0-9]{30,40}\b")
_TXID = re.compile(r"\b[a-fA-F0-9]{64}\b")
_CJK = re.compile(r"[一-鿿]")

LINE_ANSWER_FIRST = ("bot_custom", "network", "ai_account")


@dataclass
class EntryIntent:
    kind: str
    line: str | None = None
    signals: dict = field(default_factory=dict)


def _kw(name: str) -> list[str]:
    return cfg.get("entry.intents", default={}).get(name, [])


def _has(low: str, words: list[str]) -> bool:
    return any(w and w.lower() in low for w in words)


def extract_handles(text: str) -> list[str]:
    return list(dict.fromkeys(_HANDLE.findall(text or "")))


def extract_addresses(text: str) -> list[str]:
    return list(dict.fromkeys(_ADDR.findall(text or "")))


def _sensitive_line(low: str) -> str | None:
    for ln in biz_lines.get_lines():
        if ln.get("sensitive") and any(k and k.lower() in low for k in ln.get("keywords", [])):
            return ln["id"]
    return None


def _is_greeting(text: str, low: str) -> bool:
    compact = "".join(low.split()).strip("!！。.~~,，?？ ")
    if not compact or len(compact) > 6:
        return False
    for g in _kw("greeting"):
        g = g.lower()
        if compact == g:
            return True
        if compact.startswith(g):
            tail = compact[len(g):]
            if not (g.isascii() and tail[:1].isalpha()):           # "hi ya" 算问候,"hire" 不算
                return True
    return False


def _signals(low: str) -> dict:
    sig = {}
    if _has(low, _kw("angry")):
        sig["angry"] = True
    if _has(low, ["急", "马上", "尽快", "asap", "urgent", "today", "今天"]):
        sig["urgency"] = "urgent"
    return sig


def classify_entry(text: str, *, has_open_order: bool = False, has_paid_order: bool = False,
                   line_hint: str | None = None) -> EntryIntent:
    text = (text or "").strip()
    low = text.lower()
    sig = _signals(low)

    # 1 verify_request:@用户名 / 地址 / 真伪词
    if extract_handles(text) or extract_addresses(text) or _has(low, _kw("verify_words")):
        return EntryIntent("verify_request", signals=sig)
    # 2 payment_issue
    if _has(low, _kw("paid_words")) and (has_open_order or has_paid_order or _has(low, _kw("negation_words"))):
        return EntryIntent("payment_issue", signals=sig)
    # 3 refund
    if _has(low, _kw("refund")):
        return EntryIntent("refund", signals=sig)
    # 4 angry:有已付订单 → P0;否则只记信号,继续往下走
    if sig.get("angry") and has_paid_order:
        return EntryIntent("angry", signals=sig)
    # 6 is_bot(先于 human_request 判断:"你是真人吗"含"真人"但问的是身份)
    if _has(low, _kw("is_bot")):
        return EntryIntent("is_bot", signals=sig)
    # 5 human_request("客服机器人/客服系统"是业务描述,不是找人)
    scrubbed = low
    for phrase in ("客服机器人", "客服系统", "客服bot", "客服 bot", "ai客服", "ai 客服", "双向客服"):
        scrubbed = scrubbed.replace(phrase, "")
    if _has(scrubbed, _kw("human_request")):
        return EntryIntent("human_request", signals=sig)
    # 7 sensitive
    s_line = _sensitive_line(low)
    if s_line:
        return EntryIntent("sensitive", line=s_line, signals=sig)
    # 8a renew:软件线的续费/升级走官方入口规则卡(不经 AI);其他业务线仍按售后
    if _has(low, ["续费", "升级", "renew", "upgrade"]):
        r_hit = biz_lines.match_line(text)
        if (r_hit and r_hit.get("id") == "software") or line_hint == "software":
            return EntryIntent("renew", line="software", signals=sig)
    # 8 support / 9 partner(命中已知问题触发词也算售后,如 mac 的"无法验证开发者")
    from . import aftercare
    if _has(low, _kw("support")) or aftercare.match_known(text):
        return EntryIntent("support", signals=sig)
    if _has(low, _kw("partner")):
        return EntryIntent("partner", signals=sig)

    hit = biz_lines.match_line(text)
    line = (hit["id"] if hit and not hit.get("sensitive") else None) or line_hint
    # 10 price_ask
    if _has(low, _kw("price_ask")):
        return EntryIntent("price_ask", line=line, signals=sig)
    # 11 buy_now:仅软件线
    if _has(low, _kw("buy_words")) and line == "software":
        return EntryIntent("buy_now", line="software", signals=sig)
    # 12 line_question
    if hit and not hit.get("sensitive") and (len(text) >= 10 or "?" in text or "？" in text or _has(low, _kw("question_words"))):
        return EntryIntent("line_question", line=hit["id"], signals=sig)
    # 12b line_mention:只点名了业务线("我想了解软件"),没有具体问题
    if hit and not hit.get("sensitive"):
        return EntryIntent("line_mention", line=hit["id"], signals=sig)
    # 13 browse
    if _has(low, _kw("browse")):
        return EntryIntent("browse", signals=sig)
    # 14 greeting
    if _is_greeting(text, low):
        return EntryIntent("greeting", signals=sig)
    return EntryIntent("unknown", line=line_hint, signals=sig)
