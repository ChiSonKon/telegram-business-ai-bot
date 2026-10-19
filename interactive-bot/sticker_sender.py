"""
极客主理人情境贴纸发送引擎 (Humanlike Sticker Engine)

在合适的聊天节点（如问候、达成一致、点赞认可、幽默互动）发送真实 Telegram 贴纸，
完全贴合 Telegram Web3 圈内极客与主理人日常斗图、发表情包的活人习惯。
"""
import asyncio
import logging
import random
from typing import Any, Dict, List
from telegram.constants import ChatAction

logger = logging.getLogger(__name__)

# 精选 Telegram 原生高频贴纸库 (涵盖猫猫、极客、点赞、搞钱、问候)
STICKER_LIBRARY: Dict[str, List[str]] = {
    "greeting": [
        "CAACAgIAAxUAAWq8UBAhAAHjgu5qJxzzTtgLBuz0RQACBQADwDZPE_lqX5qCa011PQQ",  # 👋 热情挥手
        "CAACAgQAAxUAAWq8UBdiB72IdkMbz3XbV18Sul69AAJUCgAC_vrLAAFo21at4yBlNT0E",  # 🐱 猫猫打招呼
    ],
    "agree": [
        "CAACAgIAAxUAAWq8UBDOld0DpaU4EZjh3AdI5I0FAAIVAAPANk8TzVamO2GeZOc9BA",  # 👍 点赞认可
        "CAACAgQAAxUAAWq8UBfCl-0cBfZZwfaZco4kKaelAAJcCgAC_vrLAAHJZQkgCc8tMz0E",  # 🐱 猫猫比心/妥了
    ],
    "money": [
        "CAACAgIAAxUAAWq8UBDOld0DpaU4EZjh3AdI5I0FAAIVAAPANk8TzVamO2GeZOc9BA",  # 👍 搞钱搞定
        "CAACAgQAAxUAAWq8UBfAPl5it4JxFruuVXPGFBbGAAJWCgAC_vrLAAGpcax-ZZnLsD0E",  # 🐱 暴富开心
    ],
    "laugh": [
        "CAACAgIAAxUAAWq8UBAThnXsew6os3YpSeCVMlbyAAIBAAPANk8TGC5zMKs_LVE9BA",  # 😂 开心大笑
    ],
    "cool": [
        "CAACAgQAAxUAAWq8UBfAPl5it4JxFruuVXPGFBbGAAJWCgAC_vrLAAGpcax-ZZnLsD0E",  # 🐱 帅气从容
        "CAACAgIAAxUAAWq8UBDOld0DpaU4EZjh3AdI5I0FAAIVAAPANk8TzVamO2GeZOc9BA",  # 👍 稳稳的
    ],
    "thinking": [
        "CAACAgQAAxUAAWq8UBffGnjWpl_-drK29lyZv_rxAAL2AAMiRWEL_k0sFr8xBhM9BA",  # 🐱 思考琢磨
    ],
}


def get_random_sticker(category: str = "agree") -> str | None:
    """获取指定分类下的随机贴纸 file_id"""
    stickers = STICKER_LIBRARY.get(category) or STICKER_LIBRARY.get("agree")
    return random.choice(stickers) if stickers else None


async def send_humanlike_sticker(
    bot,
    chat_id: int,
    category: str = "agree",
    business_connection_id: str | None = None,
) -> Any:
    """
    模拟真人挑选并发送贴纸：
    1. 发送 CHOOSE_STICKER 状态
    2. 等待 0.6~1.2 秒模拟挑选
    3. 发送对应情境的贴纸
    """
    file_id = get_random_sticker(category)
    if not file_id:
        return None

    try:
        kwargs_action = {"chat_id": chat_id, "action": ChatAction.CHOOSE_STICKER}
        if business_connection_id:
            kwargs_action["business_connection_id"] = business_connection_id
        await bot.send_chat_action(**kwargs_action)
    except Exception:
        pass

    await asyncio.sleep(random.uniform(0.6, 1.2))

    kwargs_msg = {
        "chat_id": chat_id,
        "sticker": file_id,
    }
    if business_connection_id:
        kwargs_msg["business_connection_id"] = business_connection_id

    try:
        sent = await bot.send_sticker(**kwargs_msg)
        return sent
    except Exception as e:
        logger.warning(f"send_humanlike_sticker failed: {e}")
        if business_connection_id:
            try:
                kwargs_msg.pop("business_connection_id", None)
                sent = await bot.send_sticker(**kwargs_msg)
                return sent
            except Exception:
                pass
        return None
