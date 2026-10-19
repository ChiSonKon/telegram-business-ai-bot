"""进程级共享实例。会话版本号只约束本进程的在途任务,无需跨重启持久化(重启后在途任务本就不存在)。"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from .breaker import CircuitBreaker
from .epoch import EpochStore
from .lang import LangState
from .shield import Dedup, Shield

shield = Shield()
dedup = Dedup()
epochs = EpochStore()
llm_breaker = CircuitBreaker(threshold=3, cooldown=30.0)

_langs: "OrderedDict[int, LangState]" = OrderedDict()


def epoch_key(user_id: int, owner_id: int = 0) -> str:
    return f"{owner_id}:{user_id}"


def lang_for(user_id: int, text: str | None = None) -> str:
    """读取(并可选地根据新消息更新)客户语言。"""
    st = _langs.pop(user_id, None) or LangState()
    _langs[user_id] = st
    while len(_langs) > 50000:
        _langs.popitem(last=False)
    return st.update(text) if text else st.current


def force_lang(user_id: int, lang: str) -> None:
    lang_for(user_id)
    _langs[user_id].force(lang)


def is_lang_locked(user_id: int) -> bool:
    st = _langs.get(user_id)
    return bool(st and st.locked)


# ---------- 时间:唯一"当前时间"来源,测试可平移 ----------
_clock_offset = timedelta(0)


def now() -> datetime:
    """当前 UTC 时间(带时区)。测试通过 shift_time 平移,避免 sleep。"""
    return datetime.now(timezone.utc) + _clock_offset


def shift_time(**kw) -> None:
    global _clock_offset
    _clock_offset += timedelta(**kw)


def reset_time() -> None:
    global _clock_offset
    _clock_offset = timedelta(0)
