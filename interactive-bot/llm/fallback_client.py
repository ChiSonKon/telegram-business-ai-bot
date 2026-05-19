"""支持多 AI 配置自动降级的 LLM 客户端。"""
from __future__ import annotations

import logging

from .client import LLMClient
from ..ai_settings import get_enabled_ai_configs, promote_ai_config

logger = logging.getLogger(__name__)


class FallbackLLMClient:
    """每次请求读取最新后台配置，并按优先级自动切换备用 AI。"""

    def __init__(self):
        self._clients: dict[str, tuple[tuple[str, str, str], LLMClient]] = {}

    def _client_for(self, config: dict) -> LLMClient:
        signature = (config["base_url"], config["api_key"], config["model"])
        cached = self._clients.get(config["id"])
        if cached and cached[0] == signature:
            return cached[1]
        client = LLMClient(base_url=config["base_url"], api_key=config["api_key"], model=config["model"])
        self._clients[config["id"]] = (signature, client)
        return client

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict],
        rag_context: str = "",
        long_term_context: str = "",
    ) -> str:
        configs = get_enabled_ai_configs()
        errors: list[str] = []
        for index, config in enumerate(configs):
            try:
                logger.info(
                    "Trying AI config: name=%s model=%s base_url=%s",
                    config["name"],
                    config["model"],
                    config["base_url"],
                )
                reply = await self._client_for(config).chat(
                    system_prompt=system_prompt,
                    messages=messages,
                    rag_context=rag_context,
                    long_term_context=long_term_context,
                )
                if index > 0:
                    promote_ai_config(config["id"])
                    logger.warning("Primary AI failed; promoted fallback AI: %s", config["name"])
                return reply
            except Exception as exc:
                err = f"{config['name']}({config['model']}): {exc}"
                errors.append(err)
                logger.error("AI config failed: %s", err)
        raise RuntimeError("所有 AI 配置均调用失败：" + " | ".join(errors))