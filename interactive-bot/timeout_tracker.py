"""
客户交互超时关怀调度器：
当 AI 发送回复后，如果客户超过 10 分钟（600秒）没有回复，并且当前仍在 AI 模式：
自动向客户推送关怀卡片：
『您好，刚刚接待您的是白猫AI分身，是否需要转接人工客服？』
附带按钮：[ 转接人工客服（红）] [ 继续与 AI 分身沟通（蓝）]
"""
import time
import logging
from typing import Dict, Any
from .middleware.state_machine import is_ai_mode

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 600  # 10 分钟
AUTO_FOLLOWUP = False

# 内存会话跟踪池: user_id -> dict
_active_sessions: Dict[int, Dict[str, Any]] = {}


def track_activity(
    user_id: int,
    chat_id: int,
    business_connection_id: str | None = None,
    is_ai_reply: bool = False,
):
    """记录用户或 AI 的交互行为"""
    now = time.time()
    if user_id not in _active_sessions:
        _active_sessions[user_id] = {
            "chat_id": chat_id,
            "business_connection_id": business_connection_id,
            "last_activity_time": now,
            "waiting_user_reply": is_ai_reply,
            "care_prompt_sent": False,
        }
    else:
        s = _active_sessions[user_id]
        s["chat_id"] = chat_id
        if business_connection_id is not None:
            s["business_connection_id"] = business_connection_id
        s["last_activity_time"] = now
        if is_ai_reply:
            s["waiting_user_reply"] = True
            s["care_prompt_sent"] = False
        else:
            s["waiting_user_reply"] = False
            s["care_prompt_sent"] = False


def dismiss_care(user_id: int):
    """用户点击继续 AI 或转人工后，解除等待状态"""
    if user_id in _active_sessions:
        _active_sessions[user_id]["care_prompt_sent"] = True
        _active_sessions[user_id]["waiting_user_reply"] = False


async def check_timeout_care(bot):
    """定期巡检（由 APScheduler 每 30 秒调用一次）"""
    if not AUTO_FOLLOWUP:      # policy.handoff.automatic_idle_followup=false:默认不主动追问沉默客户
        return
    now = time.time()
    for user_id, s in list(_active_sessions.items()):
        if not s.get("waiting_user_reply"):
            continue
        if s.get("care_prompt_sent"):
            continue
        if not is_ai_mode(user_id):
            continue

        elapsed = now - s.get("last_activity_time", now)
        if elapsed >= TIMEOUT_SECONDS:
            s["care_prompt_sent"] = True
            chat_id = s["chat_id"]
            biz_conn_id = s.get("business_connection_id")

            from .ui import send_card
            from .ui.cards import CARE_TEXT, care_markup

            sent = await send_card(bot, chat_id, CARE_TEXT, care_markup(user_id), biz_conn_id)
            if sent:
                logger.info(f"已向用户 {user_id} 发送 10 分钟未回复关怀提醒")
