"""
AI/人工 状态机 — 控制每个用户的对话模式

AI_MODE:    AI 自动回复（默认）
HUMAN_MODE: 人工接管，阻断 LLM
"""
import logging
from enum import Enum

from db.database import SessionMaker
from db.model import User

logger = logging.getLogger(__name__)


class ChatMode(Enum):
    AI_MODE = "AI_MODE"
    HUMAN_MODE = "HUMAN_MODE"


def get_chat_mode(user_id: int) -> ChatMode:
    """获取用户当前的对话模式"""
    db = SessionMaker()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user and user.chat_mode == "HUMAN_MODE":
            return ChatMode.HUMAN_MODE
        return ChatMode.AI_MODE
    finally:
        db.close()


def set_chat_mode(user_id: int, mode: ChatMode):
    """设置用户的对话模式"""
    db = SessionMaker()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user:
            user.chat_mode = mode.value
            db.add(user)
            db.commit()
            logger.info(f"用户 {user_id} 模式已切换为 {mode.value}")
        else:
            logger.warning(f"用户 {user_id} 不存在，无法切换模式")
    finally:
        db.close()


def is_ai_mode(user_id: int) -> bool:
    """快捷判断：是否处于 AI 模式"""
    return get_chat_mode(user_id) == ChatMode.AI_MODE


def is_human_mode(user_id: int) -> bool:
    """快捷判断：是否处于人工模式"""
    return get_chat_mode(user_id) == ChatMode.HUMAN_MODE
