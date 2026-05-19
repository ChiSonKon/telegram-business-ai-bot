"""
动态 System Prompt 加载器 — 每次调用 LLM 前实时读取，支持热更新
"""
import logging
from ..prompt_settings import get_system_prompt

logger = logging.getLogger(__name__)

def load_system_prompt(filepath: str = None) -> str:
    """
    实时读取 System Prompt。
    优先从数据库读取，老板可以随时在 Telegram 管理后台修改。
    """
    return get_system_prompt()
