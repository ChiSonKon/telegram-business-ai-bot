"""会话版本号:人工接管/编辑/停止生成时 bump,所有在途任务在每个副作用前校验。"""
from __future__ import annotations

from typing import Callable


class Superseded(Exception):
    """在途任务已过期,应静默放弃。"""


class EpochStore:
    """内存实现;生产中以 FrontdeskCase 版本列持久化,接口保持一致。"""

    def __init__(self, persist: Callable[[str, int], None] | None = None):
        self._v: dict[str, int] = {}
        self._persist = persist

    def current(self, key: str) -> int:
        return self._v.get(key, 0)

    def bump(self, key: str) -> int:
        v = self._v[key] = self.current(key) + 1
        if self._persist:
            self._persist(key, v)   # 先持久化再通知
        return v

    def ticket(self, key: str) -> "Ticket":
        return Ticket(self, key, self.current(key))


class Ticket:
    def __init__(self, store: EpochStore, key: str, version: int):
        self.store, self.key, self.version = store, key, version

    @property
    def valid(self) -> bool:
        return self.store.current(self.key) == self.version

    def check(self) -> None:
        if not self.valid:
            raise Superseded(self.key)
