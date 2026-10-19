"""支持多 AI 配置自动降级的 LLM 客户端。"""
from __future__ import annotations

import logging

from .client import LLMClient
from ..ai_settings import _default_ai_configs, get_enabled_ai_configs, promote_ai_config

logger = logging.getLogger(__name__)


def _alert_primary_failed(errors: list[str], used: str) -> None:
    """首选 AI 失败、靠备用成功时也提醒管理群(半小时一次),否则后台配置错了很久都没人知道。"""
    try:
        import html
        import time
        from ..frontdesk.aftercare import mask_keys
        from ..outbox import enqueue_admin
        reason = mask_keys(" ".join(errors))[:300]
        enqueue_admin(f"llm_primary_failed:{int(time.time() // 1800)}",
                      "⚠️ <b>首选 AI 配置调用失败,已由备用配置接管</b>\n" + html.escape(reason) + "\n"
                      + f"当前使用:{html.escape(used)}。请到「设置 → AI 引擎」检查首选配置的密钥/模型。")
    except Exception:
        logger.warning("primary-failed alert not queued")


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
        configs = list(get_enabled_ai_configs())
        # 末位兜底:.env 里的默认 AI(密钥不同于后台已配置的任何一个时)。后台填错/过期的密钥不会让 AI 整体瘫痪。
        try:
            env_cfg = dict(_default_ai_configs()[0])
            if env_cfg.get("api_key") and env_cfg.get("base_url") and all(env_cfg["api_key"] != c["api_key"] for c in configs):
                env_cfg.update(id="env-fallback", name="环境默认AI(兜底)")
                configs.append(env_cfg)
        except Exception:
            pass
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
                    _alert_primary_failed(errors, config["name"])
                return reply
            except Exception as exc:
                err = f"{config['name']}({config['model']}): {exc}"
                errors.append(err)
                logger.error("AI config failed: %s", err)
        raise RuntimeError("所有 AI 配置均调用失败：" + " | ".join(errors))