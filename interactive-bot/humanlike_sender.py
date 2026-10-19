"""
拟真人类消息发送引擎 (Human-like Messaging Engine)

融合文字打字态、多气泡拆分、原生语音条 (.ogg) 与情境贴纸 (Stickers) 的多模态活人交互引擎：
1. 拟真打字态 (Typing Simulation)：按文本长度动态等待 1.0~2.5 秒，模拟真人在手机键盘打字。
2. 极简气泡控制 (Bubble Control)：120字内严格单气泡，超过最多拆 2 个气泡，杜绝刷屏。
3. 原生语音条 (Voice Notes)：支持以微软云希真声录制 100% 逼真 Telegram 原生语音条。
4. 情境贴纸 (Stickers)：支持按情绪/意图发送猫猫、点赞、搞钱贴纸，宛如真人斗图。
"""
import asyncio
import logging
import random
import re
from typing import List, Dict, Any
from telegram.constants import ChatAction

from .voice_sender import send_humanlike_voice
from .sticker_sender import send_humanlike_sticker

logger = logging.getLogger(__name__)


async def keep_chat_action(
    bot,
    chat_id: int,
    action: str,
    duration: float,
    business_connection_id: str | None = None,
) -> None:
    """持续显示 typing/录音 等状态 duration 秒（Telegram 单次状态约 5 秒后消失，这里每 4 秒续期）。"""
    kwargs: Dict[str, Any] = {"chat_id": chat_id, "action": action}
    if business_connection_id:
        kwargs["business_connection_id"] = business_connection_id
    remaining = duration
    while remaining > 0:
        try:
            await bot.send_chat_action(**kwargs)
        except Exception:
            pass
        step = min(4.0, remaining)
        await asyncio.sleep(step)
        remaining -= step


def split_into_bubbles(text: str, max_bubbles: int = 2) -> List[str]:
    """
    将长文本智能拆分为 1~2 个符合人类聊天习惯的短气泡。
    绝大多数情况保持单气泡，杜绝一次性发送连珠炮刷屏。
    """
    clean_text = text.strip()
    if not clean_text:
        return []

    # 120 字以内的短消息绝对保持单个气泡，绝不碎发
    if len(clean_text) <= 120:
        return [clean_text]

    # 按空行或者双换行拆分自然段
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', clean_text) if p.strip()]

    # 仅 1 个自然段，直接返回
    if len(paragraphs) <= 1:
        return [clean_text]

    # 刚好 2 个自然段
    if len(paragraphs) == 2:
        return paragraphs

    # 超过 2 个自然段时，智能折半合并为最多 2 个气泡
    mid = len(paragraphs) // 2
    b1 = "\n\n".join(paragraphs[:mid])
    b2 = "\n\n".join(paragraphs[mid:])
    return [b1, b2]


async def send_humanlike_messages(
    bot,
    chat_id: int,
    text: str,
    business_connection_id: str | None = None,
    parse_mode: str | None = None,
    force_voice: bool = False,
    should_continue=None,
    lang: str = "zh",
) -> List[Any]:
    """
    多模态拟真消息发送：智能解析文字、语音指令与贴纸，模拟真人节奏发送。
    """
    sent_messages = []
    clean_text = text.strip() if text else ""
    alive = should_continue or (lambda: True)   # 人工接管/版本失效后,任何副作用前都要复核

    # Business 会话:AI 语音以业主身份发出,可能被误认为真人,默认禁用(D7);Bot 私聊语音前先说明是 AI 语音
    from .config import cfg, copy
    voice_allowed = (not business_connection_id) or bool(cfg.get("experience.voice.business_enabled", default=False))

    async def _voice_label():
        if business_connection_id:
            return
        try:
            m = await bot.send_message(chat_id=chat_id, text=copy("ai_voice_label", lang))
            sent_messages.append(m)
        except Exception:
            pass

    # ═══ 1. 强制语音模式处理 ═══
    if force_voice and voice_allowed and clean_text and alive():
        await _voice_label()
        voice_msg = await send_humanlike_voice(
            bot=bot,
            chat_id=chat_id,
            text=clean_text,
            business_connection_id=business_connection_id,
        )
        if voice_msg:
            sent_messages.append(voice_msg)
            return sent_messages

    # ═══ 2. 检查并提取 [VOICE: ...] 语音标签 ═══
    voice_content = None
    voice_match = re.search(r'\[VOICE:\s*([^\]]+)\]', clean_text)
    if voice_match:
        voice_content = voice_match.group(1).strip()
        clean_text = clean_text.replace(voice_match.group(0), "").strip()

    # ═══ 3. 检查并提取 [STICKER: ...] 贴纸标签 ═══
    sticker_cat = None
    sticker_match = re.search(r'\[STICKER:\s*([a-zA-Z0-9_-]+)\]', clean_text)
    if sticker_match:
        sticker_cat = sticker_match.group(1).strip().lower()
        clean_text = clean_text.replace(sticker_match.group(0), "").strip()

    # ═══ 4. 发送文本气泡（如有文本）═══
    bubbles = split_into_bubbles(clean_text, max_bubbles=2)
    if bubbles:
        # 先"读一下"再开始打字，更像真人
        await asyncio.sleep(random.uniform(0.4, 1.1))
    for i, bubble in enumerate(bubbles):
        char_count = len(bubble)
        if i == 0:
            delay = min(max(char_count * 0.03, 1.0), 2.2) + random.uniform(0.1, 0.3)
        else:
            delay = min(max(char_count * 0.025, 0.8), 1.8) + random.uniform(0.1, 0.3)

        # Telegram 原生 typing 状态，按间隔续期直到发送
        if not alive():
            return sent_messages
        await keep_chat_action(bot, chat_id, ChatAction.TYPING, delay, business_connection_id)
        if not alive():
            return sent_messages

        kwargs_msg: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": bubble,
            "parse_mode": parse_mode,
        }
        if business_connection_id:
            kwargs_msg["business_connection_id"] = business_connection_id

        try:
            msg = await bot.send_message(**kwargs_msg)
            sent_messages.append(msg)
        except Exception as e:
            # 不降级为以 Bot 身份私聊:Business 会话必须以原身份发送,失败即如实失败
            logger.warning(f"send_humanlike_messages bubble {i+1} failed: {e}")
            break

    # ═══ 5. 发送语音备忘录（如有语音标签）═══
    if voice_content and voice_allowed and alive():
        await _voice_label()
        voice_msg = await send_humanlike_voice(
            bot=bot,
            chat_id=chat_id,
            text=voice_content,
            business_connection_id=business_connection_id,
        )
        if voice_msg:
            sent_messages.append(voice_msg)

    # ═══ 6. 发送贴纸（显式标签，或自然概率点赞）═══
    # 自然概率贴纸：如果文本中带有明显的达成一致或点赞词（妥了/没问题/稳/放心），且没发语音，20%几率自然补发贴纸
    if not sticker_cat and not voice_content:
        agree_triggers = ["妥了", "没问题", "稳", "放心", "包在我身上", "没毛病", "老铁"]
        if any(w in clean_text for w in agree_triggers) and random.random() < 0.20:
            sticker_cat = "agree"

    if sticker_cat and alive():
        st_msg = await send_humanlike_sticker(
            bot=bot,
            chat_id=chat_id,
            category=sticker_cat,
            business_connection_id=business_connection_id,
        )
        if st_msg:
            sent_messages.append(st_msg)

    return sent_messages
