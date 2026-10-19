"""AI 回合的"思考中"占位(03 §8.6,仅 Bot 私聊):sendMessageDraft 空文本,客户端显示 Thinking…,可点停止。

草稿是 30 秒临时预览,后台每 20 秒刷新一次直到回复就绪;回复以普通 sendMessage 发出,草稿自动消失。
客户点"停止" → stopped_message_generation 更新 → 找到在途回合并让它作废(epoch 递增),不发送任何内容。
失败(旧客户端/接口报错)→ 关闭该能力 24 小时,回退到现有的 typing 状态。
"""
from __future__ import annotations

import asyncio
import logging

from ..config import cfg
from . import capabilities, tgx

logger = logging.getLogger(__name__)
REFRESH_S = 20.0
MAX_REFRESHES = 9                                                          # 约 3 分钟
_active: dict[tuple[int, int], "Draft"] = {}


def draft_id_for(case_id: int | None, turn_no: int) -> int:
    return max(1, (case_id or 0) * 1000 + max(turn_no, 0) + 1)         # 非零


class Draft:
    def __init__(self, bot, chat_id: int, user_id: int, draft_id: int):
        self.bot, self.chat_id, self.user_id, self.draft_id = bot, chat_id, user_id, draft_id
        self._task: asyncio.Task | None = None
        self.stopped = False

    async def start(self) -> bool:
        if not await tgx.send_draft(self.bot, self.chat_id, self.draft_id, "", True):
            capabilities.fail("bot", "draft")
            return False
        _active[(self.chat_id, self.draft_id)] = self
        self._task = asyncio.create_task(self._refresh())
        return True

    async def _refresh(self):
        try:
            for _ in range(MAX_REFRESHES):                                 # 有上限:回合异常未 stop 也不会无限刷新
                await asyncio.sleep(REFRESH_S)
                if self.stopped:
                    break
                await tgx.send_draft(self.bot, self.chat_id, self.draft_id, "", True)
            _active.pop((self.chat_id, self.draft_id), None)
        except asyncio.CancelledError:
            pass

    def stop(self) -> None:
        self.stopped = True
        _active.pop((self.chat_id, self.draft_id), None)
        if self._task and not self._task.done():
            self._task.cancel()


async def begin(bot, chat_id: int, user_id: int, case_id: int | None, turn_no: int, biz: str | None = None) -> "Draft | None":
    """Business 与被关闭的能力不启用。返回 Draft 或 None。"""
    if biz or not cfg.get("experience.drafts.enabled", default=True) or not capabilities.ok("bot", "draft"):
        return None
    d = Draft(bot, chat_id, user_id, draft_id_for(case_id, turn_no))
    return d if await d.start() else None


def handle_stop(chat_id: int, draft_id: int) -> int | None:
    """处理客户点"停止":返回被停止回合的 user_id(没有在途回合返回 None)。调用方负责 epochs.bump。"""
    d = _active.get((chat_id, draft_id))
    if not d:
        return None
    d.stop()
    return d.user_id


async def on_stop_generation(update, context) -> None:
    """客户在"思考中"占位上点了停止(Update.api_kwargs 里的 stopped_message_generation):让在途回合作废,不发送任何内容。"""
    info = (getattr(update, "api_kwargs", None) or {}).get("stopped_message_generation")
    if not info:
        return
    try:
        chat_id = int((info.get("chat") or {}).get("id"))
        draft_id = int(info.get("draft_id"))
    except (TypeError, ValueError):
        return
    uid = handle_stop(chat_id, draft_id)
    if uid is None:
        return
    from whitecat_core.runtime import epoch_key, epochs
    epochs.bump(epoch_key(uid))
    try:
        from ..analytics.events import emit
        emit("ai_stopped", uid)
    except Exception:
        pass
