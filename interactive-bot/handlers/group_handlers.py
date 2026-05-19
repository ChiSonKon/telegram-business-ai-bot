"""
群组消息处理 — 监听群组中 @bot_username 的消息

仅当文本包含 @bot_username 或 MENTION 实体时，
提取问题 → 请求 LLM → reply_to_message_id 回复
"""
from telegram import Update
from telegram.ext import ContextTypes

from .. import admin_group_id, logger, bot_username
from ..llm.fallback_client import FallbackLLMClient
from ..llm.prompt_loader import load_system_prompt
from ..rag.retriever import search_knowledge

# LLM 客户端
_llm_client = FallbackLLMClient()


def _is_mentioned(update: Update) -> bool:
    """检查消息是否提及了 Bot"""
    if not update.message or not update.message.text:
        return False

    text = update.message.text

    # 检查 @bot_username
    if bot_username and f"@{bot_username}" in text:
        return True

    # 检查 MENTION entity
    if update.message.entities:
        for entity in update.message.entities:
            if entity.type == "mention":
                mention_text = text[entity.offset : entity.offset + entity.length]
                if bot_username and mention_text.lower() == f"@{bot_username.lower()}":
                    return True

    return False


def _extract_question(update: Update) -> str:
    """提取问题文本（去掉 @bot_username）"""
    text = update.message.text or ""
    if bot_username:
        text = text.replace(f"@{bot_username}", "").strip()
    return text


async def handle_group_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    群组 @mention 处理器。
    仅响应包含 @bot_username 的消息。
    """
    # 跳过管理群
    if update.effective_chat.id == admin_group_id:
        return

    if not _is_mentioned(update):
        return

    question = _extract_question(update)
    if not question:
        await update.message.reply_text("🤖 你好！请问有什么问题？")
        return

    logger.info(f"群组 @mention: chat={update.effective_chat.id}, question={question[:50]}")

    try:
        # RAG 检索
        rag_context = search_knowledge(question)

        # 加载 System Prompt
        system_prompt = load_system_prompt()

        # LLM 推理（群组消息不使用记忆）
        ai_reply = await _llm_client.chat(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": question}],
            rag_context=rag_context,
        )

        await update.message.reply_text(
            ai_reply,
            reply_to_message_id=update.message.message_id,
        )

    except Exception as e:
        logger.error(f"群组回复失败: {e}", exc_info=True)
        await update.message.reply_text(
            "⚠️ AI 暂时离线，请稍后重试。",
            reply_to_message_id=update.message.message_id,
        )
