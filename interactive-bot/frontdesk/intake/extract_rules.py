"""规则抽取器(03 §5.4):每条客户消息都跑,纯函数,可单测。"""
from __future__ import annotations

import re

CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNIT = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_CN = "零〇一二两三四五六七八九十百千万"
_CN_NUM = re.compile(f"[{_CN}]+")


def cn_num(s: str) -> int | None:
    """中文数字 → 整数:两千=2000,五千=5000,三万=30000,一千五=1500,十=10,二十=20。解析失败返回 None。"""
    s = (s or "").strip()
    if not s or any(ch not in _CN for ch in s):
        return None
    total, section, last_unit, num = 0, 0, None, None
    for ch in s:
        if ch in CN_DIGIT:
            num = CN_DIGIT[ch]
        else:
            unit = CN_UNIT[ch]
            if unit == 10000:
                section = (section + (num or 0)) or 1
                total += section * 10000
                section, num, last_unit = 0, None, unit
                continue
            if num is None:
                num = 1 if unit == 10 else None
                if num is None:
                    return None
            section += num * unit
            num, last_unit = None, unit
    if num is not None:
        if last_unit and last_unit >= 100 and section and num < 10:          # 一千五 → 1500
            section += num * (last_unit // 10)
        else:
            section += num
    return total + section


# ---------- money ----------
_UNITS = r"(?:usdt|u|刀|美元|美金|\$|usd|元|块|rmb|cny|k)"
_NUM = r"\d+(?:\.\d+)?"
_RE_RANGE = re.compile(rf"(?<![\d.])({_NUM})\s*[-–—~到至]\s*({_NUM})\s*({_UNITS})?(?![A-Za-z])", re.I)
_RE_AMT = re.compile(rf"(\$)?\s*({_NUM})\s*({_UNITS})?(?![A-Za-z])", re.I)
_RE_CN = re.compile(rf"([{_CN}]+)\s*({_UNITS})?\s*(左右|上下|以内|以下|之内)?")
_FUZZ = re.compile(r"(左右|上下|大概|约|差不多)")
_UPTO = re.compile(r"(以内|以下|之内|不超过|最多|封顶)")


def _cur(unit: str | None) -> str:
    u = (unit or "").lower()
    if u in ("元", "块", "rmb", "cny"):
        return "CNY"
    if u in ("$", "usd", "美元", "美金"):
        return "USD"
    return "U"


def _scale(num: float, unit: str | None) -> float:
    return num * 1000 if (unit or "").lower() == "k" else num


def money(text: str) -> dict | None:
    """{"amount","currency","lo","hi","raw"};区间/以内/左右按区间表示,amount 取代表值(上限或中点)。无金额返回 None。"""
    t = (text or "").replace(",", "").replace("，", "")
    low = t.lower()
    m = _RE_RANGE.search(t)
    if m and (m.group(3) or int(float(m.group(2))) >= 50):
        lo, hi = float(m.group(1)), float(m.group(2))
        unit = m.group(3)
        lo, hi = _scale(lo, unit), _scale(hi, unit)
        if lo <= hi:
            return {"amount": (lo + hi) / 2, "currency": _cur(unit), "lo": lo, "hi": hi, "raw": m.group(0)}
    m = _RE_CN.search(t)
    if m:
        n = cn_num(m.group(1))
        # 单独的"一/两/三…"不是金额;至少要带 百/千/万 或明确货币单位
        if n is not None and (any(u in m.group(1) for u in "百千万") or m.group(2)) and n >= 10:
            tail = m.group(3) or ""
            if tail in ("左右", "上下"):
                return {"amount": float(n), "currency": _cur(m.group(2)), "lo": n * 0.8, "hi": n * 1.2, "raw": m.group(0)}
            if tail in ("以内", "以下", "之内"):
                return {"amount": float(n), "currency": _cur(m.group(2)), "lo": 0.0, "hi": float(n), "raw": m.group(0)}
            return {"amount": float(n), "currency": _cur(m.group(2)), "lo": float(n), "hi": float(n), "raw": m.group(0)}
    for m in _RE_AMT.finditer(t):
        unit = m.group(3) or ("$" if m.group(1) else None)
        if not unit:
            continue                                                     # 无单位的裸数字不算金额(可能是数量/年份)
        n = _scale(float(m.group(2)), unit)
        tail = t[m.end():m.end() + 4]
        if _UPTO.search(tail):
            return {"amount": n, "currency": _cur(unit), "lo": 0.0, "hi": n, "raw": m.group(0)}
        if _FUZZ.search(tail):
            return {"amount": n, "currency": _cur(unit), "lo": n * 0.8, "hi": n * 1.2, "raw": m.group(0)}
        return {"amount": n, "currency": _cur(unit), "lo": n, "hi": n, "raw": m.group(0)}
    m = re.search(rf"({_NUM})\s*(以内|以下|之内)", t)
    if m and float(m.group(1)) >= 50:
        n = float(m.group(1))
        return {"amount": n, "currency": "U", "lo": 0.0, "hi": n, "raw": m.group(0)}
    return None


def to_units(m: dict, cny_per_u: float = 7.0) -> float:
    """代表金额折算为 U(USDT≈USD;人民币按 cny_per_u)。"""
    amt = m["amount"]
    return amt / cny_per_u if m["currency"] == "CNY" else amt


# ---------- 其他槽位 ----------
def qty(text: str) -> int | None:
    m = re.search(r"(\d+)\s*(个|台|套|张|账号|号|ip|条)", text or "", re.I)
    if m:
        return int(m.group(1))
    m = re.search(rf"([{_CN}]+)\s*(个|台|套|张|账号|号|条)", text or "")
    return cn_num(m.group(1)) if m else None


def devices(text: str) -> int | None:
    m = re.search(r"(\d+)\s*(?:台|部)?\s*(?:电脑|设备|机器|devices?|computers?|pcs?)", text or "", re.I)
    if m:
        return int(m.group(1))
    m = re.search(rf"([{_CN}]+)\s*(?:台|部)\s*(?:电脑|设备|机器)?", text or "")
    if m:
        return cn_num(m.group(1))
    m = re.search(rf"([{_CN}]+)\s*(?:电脑|设备)", text or "")
    return cn_num(m.group(1)) if m else None


def os_(text: str) -> str | None:
    low = (text or "").lower()
    if re.search(r"\bintel\b|英特尔", low) and re.search(r"mac|苹果", low):
        return "macos_intel"
    if re.search(r"\bm[1-4]\b|apple\s*silicon|苹果芯片|mac|macbook|苹果电脑|苹果", low):
        return "macos_arm"
    if re.search(r"win(?:dows)?|微软|\bpc\b", low):
        return "windows"
    return None


def horizon(text: str) -> str | None:
    low = (text or "").lower()
    if re.search(r"终身|永久|长期|lifetime|forever|多台", low) and re.search(r"多台|几台|\d+\s*台|两台|三台|devices|multiple", low):
        return "multi"
    if re.search(r"试用|试试|先试|几天|三天|trial|try", low):
        return "trial"
    if re.search(r"一个月|1\s*个月|月卡|按月|一月|monthly|a month|1 month", low):
        return "1m"
    if re.search(r"三个月|3\s*个月|季|quarter|3 months|three months", low):
        return "3m"
    if re.search(r"一年|1\s*年|年卡|年度|annual|a year|1 year|yearly", low):
        return "1y"
    if re.search(r"终身|永久|长期|lifetime|forever|long.?term", low):
        return "multi"
    return None


def urgency(text: str) -> str | None:
    return "urgent" if re.search(r"急|马上|尽快|立刻|今天|asap|urgent|right away", (text or "").lower()) else None


def deadline(text: str) -> str | None:
    low = (text or "").lower()
    if re.search(r"这周|本周|一周|1\s*周|一星期|七天|7\s*天|asap|today|this week|within a week|马上|尽快|今天|明天", low):
        return "week"
    if re.search(r"本月|一个月|1\s*个月|月内|30\s*天|within a month|this month|next month", low):
        return "month"
    if re.search(r"不急|不着急|慢慢来|没有要求|no rush|whenever|不赶", low):
        return "none"
    return None


_ANGRY = ("骗子", "垃圾", "投诉", "跑路", "scam", "fraud", "scammer", "rip off", "ripoff")
_ANXIOUS = ("怎么还没", "到底", "着急", "等了", "还没到", "还没回", "why hasn't", "still waiting", "where is")


def sentiment(text: str) -> str:
    low = (text or "").lower()
    if any(k in low for k in _ANGRY):
        return "angry"
    if any(k in low for k in _ANXIOUS):
        return "anxious"
    return "calm"


def trust_concern(text: str) -> bool:
    return bool(re.search(r"靠谱吗|可靠吗|真的假的|会不会跑路|被骗|安全吗|legit|is it safe|trustworthy|real or fake", (text or "").lower()))


def company(text: str) -> bool:
    low = (text or "").lower()
    for phrase in ("你们公司", "贵公司", "你们团队", "贵司", "your company", "your team"):
        low = low.replace(phrase, "")
    return bool(re.search(r"公司|团队|企业|我们老板|合同|发票|对公|company|team of|enterprise|invoice|contract", low))


def order_ref(text: str) -> dict:
    out = {}
    m = re.search(r"\b[a-fA-F0-9]{64}\b", text or "")
    if m:
        out["txid"] = m.group(0)
    m = re.search(r"\b[A-F0-9]{10}\b", text or "")
    if m:
        out["order_id"] = m.group(0)
    return out


def reseller(text: str) -> bool:
    return bool(re.search(r"代理|分销|渠道|拿货|底价|reseller|wholesale|distributor", (text or "").lower()))


def questions(text: str) -> int:
    """问题数:问号计数与"含疑问词的分句"计数取较大者(用于提示 AI 逐条回答)。"""
    t = text or ""
    marks = len(re.findall(r"[?？]", t))
    clauses = [c for c in re.split(r"[?？。;;\n]", t) if c.strip()]
    qs = sum(1 for c in clauses if re.search(r"吗|呢|多少|怎么|如何|能不能|可以|会不会|有没有|多久|是否|\bhow\b|\bwhat\b|\bcan\b|\bdoes\b", c.lower()))
    return max(marks, qs)


def extract_all(text: str) -> dict:
    """所有规则抽取器的合并结果,只含有值的字段。"""
    t = text or ""
    out: dict = {}
    m = money(t)
    if m:
        out["money"] = m
    for key, fn in (("qty", qty), ("devices", devices), ("os", os_), ("horizon", horizon), ("urgency", urgency), ("deadline", deadline)):
        v = fn(t)
        if v is not None:
            out[key] = v
    s = sentiment(t)
    if s != "calm":
        out["sentiment"] = s
    for key, fn in (("trust_concern", trust_concern), ("company", company), ("reseller", reseller)):
        if fn(t):
            out[key] = True
    ref = order_ref(t)
    if ref:
        out["order_ref"] = ref
    q = questions(t)
    if q:
        out["questions"] = q
    return out
