"""
LLM 客户端 — 统一对接 OpenAI / DeepSeek / Ollama / 任何兼容 v1 API
"""
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class LLMClient:
    """
    解耦的 LLM 调用层，支持：
    - OpenAI:      base_url="https://api.openai.com/v1"
    - DeepSeek:    base_url="https://api.deepseek.com/v1"
    - Ollama 本地: base_url="http://localhost:11434/v1", api_key="ollama"
    - 第三方中转:   base_url="https://your-proxy.com/v1"
    """

    def __init__(self, base_url: str, api_key: str, model: str):
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=60.0,
        )
        self.model = model
        logger.info(f"LLM Client initialized: model={model}, base_url={base_url}")

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict],
        rag_context: str = "",
        long_term_context: str = "",
    ) -> str:
        """
        发起一次对话请求。

        Args:
            system_prompt: 基座系统提示词（从 sales_system_prompt.txt 加载）
            messages: 短期记忆消息列表 [{"role": "user/assistant", "content": "..."}]
            rag_context: RAG 检索到的实时业务数据
            long_term_context: 向量库召回的长期记忆
        """
        logger.info(f"====== INJECTING SYSTEM PROMPT (Length: {len(system_prompt)}) ======\n{system_prompt}\n=======================================================")
        full_messages = [{"role": "system", "content": system_prompt}]

        # 注入 RAG 实时数据
        if rag_context:
            full_messages.append({
                "role": "system",
                "content": (
                    "【实时业务数据 — 严格按此报价，禁止篡改价格】\n"
                    f"{rag_context}"
                ),
            })

        # 注入长期记忆
        if long_term_context:
            full_messages.append({
                "role": "system",
                "content": (
                    "【客户历史画像 — 以下是与该客户过往的重要对话摘要】\n"
                    f"{long_term_context}"
                ),
            })

        # 拼接短期记忆
        full_messages.extend(messages)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                temperature=0.7,
                max_tokens=1024,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise
