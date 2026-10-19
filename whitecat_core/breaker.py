"""熔断器:连续失败开路,冷却后半开探测。"""
from __future__ import annotations

import time
from typing import Callable


class CircuitBreaker:
    def __init__(self, threshold: int = 3, cooldown: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.threshold, self.cooldown, self.clock = threshold, cooldown, clock
        self._fails, self._opened_at, self._probing = 0, None, False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "half_open" if self.clock() - self._opened_at >= self.cooldown else "open"

    def allow(self) -> bool:
        s = self.state
        if s == "closed":
            return True
        if s == "half_open" and not self._probing:
            self._probing = True     # 仅放行一个探测请求
            return True
        return False

    def success(self) -> None:
        self._fails, self._opened_at, self._probing = 0, None, False

    def failure(self) -> None:
        self._probing = False
        self._fails += 1
        if self._fails >= self.threshold or self._opened_at is not None:
            self._opened_at = self.clock()
