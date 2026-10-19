import logging
from enum import Enum
from db.database import SessionMaker
from db.model import User
logger=logging.getLogger(__name__)
class ChatMode(Enum):
    AI_MODE="AI_MODE"
    HUMAN_MODE="HUMAN_MODE"
def get_chat_mode(user_id:int)->ChatMode:
    db=SessionMaker()
    try:
        user=db.query(User).filter(User.user_id==user_id).first()
        return ChatMode.HUMAN_MODE if user and user.chat_mode=="HUMAN_MODE" else ChatMode.AI_MODE
    finally: db.close()
def set_chat_mode(user_id:int, mode:ChatMode, *, admin_release:bool=False):
    if mode==ChatMode.AI_MODE and not admin_release:
        try:
            from ..frontdesk.service import is_human_locked
            if is_human_locked(user_id):
                logger.warning("AI restore blocked by frontdesk human lock: %s",user_id); return False
        except Exception: pass
    db=SessionMaker()
    try:
        user=db.query(User).filter(User.user_id==user_id).first()
        if not user: return False
        user.chat_mode=mode.value; db.add(user); db.commit()
        if mode==ChatMode.HUMAN_MODE:
            from whitecat_core.runtime import epochs, epoch_key
            epochs.bump(epoch_key(user_id))     # 先落库,再让在途 AI 任务失效
        return True
    finally: db.close()
def is_ai_mode(user_id:int)->bool: return get_chat_mode(user_id)==ChatMode.AI_MODE
def is_human_mode(user_id:int)->bool: return get_chat_mode(user_id)==ChatMode.HUMAN_MODE
