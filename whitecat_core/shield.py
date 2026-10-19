"""入口护盾:规范化、指纹、去重、洪泛与体积控制。在任何 LLM 调用之前执行。"""
from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Callable

_ZERO_WIDTH = re.compile(r"[​-‏⁠﻿]")
_SEP = re.compile(r"[\s·.。,，、\-_*~`'\"|/\\:：;；!！?？()（）\[\]【】<>《》=+]+")

MAX_CHARS = 2000


def normalize(text: str) -> str:
    return _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text or "")).strip()


def fingerprint(text: str) -> str:
    """忽略标点/空白/大小写/零宽符的内容指纹,用于识别换皮重复。"""
    core = _SEP.sub("", normalize(text).lower())
    return hashlib.sha1(core.encode()).hexdigest()[:16] if core else ""


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str = "ok"
    text: str = ""          # 规范化并截断后的文本


class Dedup:
    """按 key(如 update_id)做 LRU+TTL 去重。"""

    def __init__(self, size: int = 20000, ttl: float = 600, clock: Callable[[], float] = time.monotonic):
        self._seen: OrderedDict = OrderedDict()
        self.size, self.ttl, self.clock = size, ttl, clock

    def seen(self, key) -> bool:
        now = self.clock()
        ts = self._seen.get(key)
        if ts is not None and now - ts <= self.ttl:
            self._seen.move_to_end(key)
            return True
        self._seen[key] = now
        self._seen.move_to_end(key)
        while len(self._seen) > self.size:
            self._seen.popitem(last=False)
        return False


class Shield:
    def __init__(self, *, burst: int = 8, burst_window: float = 10.0,
                 repeat_limit: int = 3, repeat_window: float = 60.0,
                 max_chars: int = MAX_CHARS, clock: Callable[[], float] = time.monotonic):
        self.burst, self.burst_window = burst, burst_window
        self.repeat_limit, self.repeat_window = repeat_limit, repeat_window
        self.max_chars, self.clock = max_chars, clock
        self._events: dict[int, deque] = {}

    def check(self, user_id: int, text: str) -> Decision:
        """allow=False 时调用方应静默丢弃(不回复、不耗 token),reason 仅用于日志。"""
        now = self.clock()
        clean = normalize(text)
        fp = fingerprint(clean)
        dq = self._events.setdefault(user_id, deque())
        dq.append((now, fp))
        horizon = now - max(self.burst_window, self.repeat_window)
        while dq and dq[0][0] < horizon:
            dq.popleft()
        if len(self._events) > 20000:
            for k in [k for k, v in self._events.items() if not v or v[-1][0] < now - self.repeat_window]:
                self._events.pop(k, None)
        if sum(1 for t, _ in dq if t >= now - self.burst_window) > self.burst:
            return Decision(False, "flood")
        if fp and sum(1 for _, h in dq if h == fp) > self.repeat_limit:
            return Decision(False, "repeat")
        if len(clean) > self.max_chars:
            return Decision(True, "truncated", clean[: self.max_chars])
        return Decision(True, "ok", clean)
