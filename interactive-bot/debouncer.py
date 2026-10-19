"""
客户连续输入消息防抖合并器 (Message Debouncer)

当客户在连续发送短句时（如：“在吗”、“问个事”、“关于空投脚本”），
将其合并为一次对话请求，避免机器人生硬地秒级多次插嘴回复。
"""
import asyncio
import logging
from typing import Dict, Any, Callable, Coroutine

logger = logging.getLogger(__name__)

DEBOUNCE_WAIT_SECONDS = 2.5

_pending_tasks: Dict[int, asyncio.Task] = {}
_message_buffers: Dict[int, list[str]] = {}
_user_locks: Dict[int, asyncio.Lock] = {}


def _get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


async def debounce_user_message(
    user_id: int,
    text: str,
    handler_callback: Callable[[list[str]], Coroutine[Any, Any, None]],
):
    """
    接收用户消息，在 DEBOUNCE_WAIT_SECONDS 窗口内合并多条消息后执行回调
    """
    if user_id not in _message_buffers:
        _message_buffers[user_id] = []

    _message_buffers[user_id].append(text)

    # 如果已有等待任务，取消上一个定时器，重置倒计时
    if user_id in _pending_tasks and not _pending_tasks[user_id].done():
        _pending_tasks[user_id].cancel()

    async def _wait_and_trigger():
        try:
            await asyncio.sleep(DEBOUNCE_WAIT_SECONDS)
            lock = _get_user_lock(user_id)
            async with lock:
                texts = _message_buffers.pop(user_id, [])
                _pending_tasks.pop(user_id, None)
                if texts:
                    await handler_callback(texts)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Debounce handler error for {user_id}: {e}", exc_info=True)

    task = asyncio.create_task(_wait_and_trigger())
    _pending_tasks[user_id] = task
