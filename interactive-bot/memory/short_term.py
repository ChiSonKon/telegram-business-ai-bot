"""
短期记忆 — 滑动窗口，维护每个 user_id 最近 N 条对话
"""
from collections import deque, defaultdict
import logging

logger = logging.getLogger(__name__)


class ShortTermMemory:
    """
    基于内存的滑动窗口短期记忆。
    为每个 user_id 维护最近 max_messages 条消息。
    """

    def __init__(self, max_messages: int = 15):
        self.max_messages = max_messages
        self._store: dict[int, deque] = defaultdict(lambda: deque(maxlen=max_messages))

    def add(self, user_id: int, role: str, content: str):
        """
        添加一条消息到记忆中。

        Args:
            user_id: 用户 ID
            role: "user" 或 "assistant"
            content: 消息内容
        """
        self._store[user_id].append({"role": role, "content": content})

    def get_history(self, user_id: int) -> list[dict]:
        """获取指定用户的完整对话历史"""
        return list(self._store[user_id])

    def clear(self, user_id: int):
        """清空指定用户的记忆"""
        if user_id in self._store:
            self._store[user_id].clear()
            logger.info(f"已清空用户 {user_id} 的短期记忆")

    def get_last_n(self, user_id: int, n: int = 5) -> list[dict]:
        """获取最近 N 条消息"""
        history = list(self._store[user_id])
        return history[-n:] if len(history) > n else history
