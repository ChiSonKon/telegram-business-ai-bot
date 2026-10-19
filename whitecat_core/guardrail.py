"""出站护栏:模型输出送达客户前的最后一道程序校验。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_TON_ADDR = re.compile(r"\b(?:EQ|UQ|kQ|0Q)[A-Za-z0-9_\-]{46}\b")
_EVM_ADDR = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_TRON_ADDR = re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b")
_URL = re.compile(r"(?:https?://|ton://|tg://)[^\s<>\"']+", re.I)
_MONEY = re.compile(r"(\d+(?:\.\d+)?)\s*(?:u\b|usdt|usd|\$|元|ton\b|stars?\b|⭐)", re.I)
_SECRET_ASK = re.compile(r"(验证码|助记词|私钥|密码|seed phrase|private key|one[- ]time code|passcode)", re.I)
_INTERNAL = re.compile(r"(system prompt|系统提示|\[\[internal|<\|im_|api[_-]?key)", re.I)


@dataclass
class Verdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def _norm_amount(s: str) -> str:
    return s.rstrip("0").rstrip(".") if "." in s else s


def check_reply(text: str, *, allowed_urls=frozenset(), allowed_addresses=frozenset(),
                allowed_amounts=frozenset()) -> Verdict:
    """未被白名单授权的地址/链接/金额一律拒绝。金额白名单为规范化字符串,如 {'29','69'}。"""
    reasons: set[str] = set()
    for rx in (_TON_ADDR, _EVM_ADDR, _TRON_ADDR):
        if any(a not in allowed_addresses for a in rx.findall(text)):
            reasons.add("unauthorized_address")
    for u in _URL.findall(text):
        if not any(u.startswith(ok) for ok in allowed_urls):
            reasons.add("unauthorized_url")
    for m in _MONEY.findall(text):
        if _norm_amount(m) not in allowed_amounts:
            reasons.add("unauthorized_amount")
    if _SECRET_ASK.search(text):
        reasons.add("secret_request")
    if _INTERNAL.search(text):
        reasons.add("internal_leak")
    return Verdict(not reasons, sorted(reasons))
