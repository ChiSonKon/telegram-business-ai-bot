"""按 (通道, 能力) 缓存体验特性是否可用。失败自动关闭 24 小时;/probe 的探测结果长期有效。

通道:bot(Bot 私聊)/ business。能力:effect / pin / react / draft / delete / pin_service_delete。
"""
from __future__ import annotations

import json

from db.database import SessionMaker
from db.model import AppSetting

KEY = "capabilities"
FAIL_HOURS = 24

# 文档未证实的 Business 能力默认关闭(03 §8.1),需 /probe 证实后才开
DEFAULT_OFF = {("business", "effect"), ("business", "pin"), ("business", "react"), ("business", "draft")}


def _now() -> float:
    from whitecat_core.runtime import now
    return now().timestamp()


def _load() -> dict:
    with SessionMaker() as db:
        row = db.query(AppSetting).filter(AppSetting.key == KEY).first()
        try:
            return json.loads(row.value) if row and row.value else {}
        except ValueError:
            return {}


def _save(data: dict) -> None:
    with SessionMaker() as db:
        row = db.query(AppSetting).filter(AppSetting.key == KEY).first()
        text = json.dumps(data)
        if row:
            row.value = text
        else:
            db.add(AppSetting(key=KEY, value=text))
        db.commit()


def ok(channel: str, cap: str) -> bool:
    entry = _load().get(f"{channel}:{cap}")
    default = (channel, cap) not in DEFAULT_OFF
    if entry is None:
        return default
    if entry.get("until") and entry["until"] > _now():
        return False                                     # 失败冷却中
    return bool(entry.get("probed", default))


def fail(channel: str, cap: str, hours: float = FAIL_HOURS) -> None:
    data = _load()
    e = data.get(f"{channel}:{cap}", {})
    e["until"] = _now() + hours * 3600
    data[f"{channel}:{cap}"] = e
    _save(data)


def set_probe_result(channel: str, cap: str, result: bool) -> None:
    data = _load()
    data[f"{channel}:{cap}"] = {"probed": bool(result), "until": None, "at": _now()}
    _save(data)


def snapshot() -> dict:
    return {k: {"ok": ok(*k.split(":")), **v} for k, v in _load().items()}
