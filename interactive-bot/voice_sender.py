"""
极客主理人原生语音合成与发送引擎 (Humanlike Voice Engine)

使用 Microsoft Edge-TTS 高保真语音技术（zh-CN-YunxiNeural 云希，阳光、真实、年轻的技术极客男声）。
发送 Telegram 原生 Opus (.ogg) 语音备忘录格式，附带波形与倍速播放控件，100% 模拟主理人用手机录制语音条。
"""
import io
import re
import asyncio
import logging
import random
import shutil
from typing import Any
import edge_tts
from telegram.constants import ChatAction

logger = logging.getLogger(__name__)

# 默认主理人音色：微软云希（极客青年、自信、松弛自然）
DEFAULT_VOICE = "zh-CN-YunxiNeural"


def _clean_text_for_speech(text: str) -> str:
    """清理非发音符号、URL、Markdown 与 Emoji"""
    t = re.sub(r'https?://\S+', '', text)  # 去除链接
    t = re.sub(r'[*_`#~\[\]\(\)]', '', t)    # 去除 Markdown
    t = re.sub(r'[^\w\s，。！？、；：“”‘’—…·a-zA-Z0-9]', '', t)  # 过滤绝大多数非中文/标点/表情
    t = t.strip()
    return t


async def _mp3_to_ogg_opus(mp3: bytes) -> bytes | None:
    """用 ffmpeg 转为 Telegram 原生语音格式 OGG/Opus；无 ffmpeg 或失败返回 None。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
            "-c:a", "libopus", "-b:a", "48k", "-ac", "1", "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(mp3), timeout=30)
        if proc.returncode == 0 and out:
            return out
        logger.warning(f"ffmpeg opus 转码失败: {err.decode(errors='ignore')[:200]}")
    except Exception as e:
        logger.warning(f"ffmpeg opus 转码异常: {e}")
    return None


async def generate_voice_ogg(text: str, voice: str = DEFAULT_VOICE) -> tuple[bytes, str]:
    """生成语音：优先 OGG/Opus（原生语音条），否则回退 MP3。返回 (数据, 文件名)。"""
    clean = _clean_text_for_speech(text)
    if not clean:
        clean = "哈喽兄弟，有什么疑问直接发我就行。"

    communicate = edge_tts.Communicate(clean, voice, rate="+4%")
    audio_data = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data += chunk["data"]

    if not audio_data:
        return b"", "voice.mp3"
    ogg = await _mp3_to_ogg_opus(audio_data)
    return (ogg, "voice.ogg") if ogg else (audio_data, "voice.mp3")


async def send_humanlike_voice(
    bot,
    chat_id: int,
    text: str,
    business_connection_id: str | None = None,
    caption: str | None = None,
) -> Any:
    """
    模拟真人按住录音按钮发送一段真实语音条：
    1. 发送 Telegram 原生 RECORD_VOICE 状态（上方显示“正在录制语音...”）
    2. 等待 1.5~3 秒模拟录音时长
    3. 发送原生带声波波形的语音消息
    """
    clean = _clean_text_for_speech(text)
    if not clean:
        clean = text

    # 1. 模拟“正在录音...”状态
    try:
        kwargs_action = {"chat_id": chat_id, "action": ChatAction.RECORD_VOICE}
        if business_connection_id:
            kwargs_action["business_connection_id"] = business_connection_id
        await bot.send_chat_action(**kwargs_action)
    except Exception:
        pass

    # 2. 生成语音文件
    audio_bytes, filename = await generate_voice_ogg(clean)
    if not audio_bytes:
        return None

    # 模拟真实录音时间（短句录 1.5~2.5 秒）
    record_delay = min(max(len(clean) * 0.04, 1.2), 3.0) + random.uniform(0.2, 0.5)
    await asyncio.sleep(record_delay)

    # 3. 发送语音条
    voice_io = io.BytesIO(audio_bytes)
    voice_io.name = filename

    kwargs_msg = {
        "chat_id": chat_id,
        "voice": voice_io,
        "caption": caption,
    }
    if business_connection_id:
        kwargs_msg["business_connection_id"] = business_connection_id

    try:
        sent = await bot.send_voice(**kwargs_msg)
        return sent
    except Exception as e:
        logger.warning(f"send_humanlike_voice failed: {e}")
        if business_connection_id:
            try:
                kwargs_msg.pop("business_connection_id", None)
                voice_io.seek(0)
                sent = await bot.send_voice(**kwargs_msg)
                return sent
            except Exception:
                pass
        return None
