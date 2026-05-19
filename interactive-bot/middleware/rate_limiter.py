"""
令牌桶限流器 — 限制单个用户每分钟的最大请求次数
"""
import time
import logging
from collections import defaultdict
from ..global_settings import get_global_setting

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    基于滑动窗口的简易限流器。
    为每个 user_id 维护 60s 内的请求时间戳列表。
    """

    def __init__(self, default_max: int = 10):
        self.default_max = default_max
        self._timestamps: dict[int, list[float]] = defaultdict(list)

    @property
    def max_per_minute(self) -> int:
        val = get_global_setting("rate_limit_per_minute")
        return int(val) if val is not None else self.default_max

    def is_allowed(self, user_id: int) -> bool:
        """
        检查用户是否被允许发送请求。

        Returns:
            True 表示允许，False 表示被限流
        """
        now = time.time()
        # 清理 60 秒前的记录
        self._timestamps[user_id] = [
            t for t in self._timestamps[user_id] if now - t < 60
        ]

        if len(self._timestamps[user_id]) >= self.max_per_minute:
            logger.warning(f"用户 {user_id} 被限流 (已达 {self.max_per_minute} 次/分钟)")
            return False

        self._timestamps[user_id].append(now)
        return True

    def get_remaining(self, user_id: int) -> int:
        """获取用户剩余可用次数"""
        now = time.time()
        self._timestamps[user_id] = [
            t for t in self._timestamps[user_id] if now - t < 60
        ]
        return max(0, self.max_per_minute - len(self._timestamps[user_id]))
