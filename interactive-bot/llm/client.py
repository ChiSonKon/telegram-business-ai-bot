"""
LLM 客户端 — 统一对接 OpenAI / DeepSeek / Ollama / 任何兼容 v1 API
"""
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


def _is_deepseek(base_url: str, model: str = "") -> bool:
    return "deepseek" in (base_url or "").lower() or "deepseek" in (model or "").lower()


def normalize_model_name(base_url: str, model: str) -> str:
    """把常见的不规范写法归一为 DeepSeek 官方 API 模型 ID(依据官方文档 api-docs.deepseek.com 定价页):
    现行名 deepseek-flash(V4.1-Flash)、deepseek-v4-pro;旧名 deepseek-v4-flash 仍可用但已是遗留名,
    deepseek-chat / deepseek-reasoner 同属旧名。非 DeepSeek 模型原样返回。
    """
    m = (model or "").strip()
    low = m.lower()
    if not _is_deepseek(base_url, low):
        return m
    if "pro" in low:
        return "deepseek-v4-pro"
    if "flash" in low or "chat" in low or "reasoner" in low or low in ("deepseek-v4", "deepseek"):
        return "deepseek-flash"
    return low or m


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
            timeout=25.0,
            max_retries=0,   # 重试由网关统一控制,避免隐式放大成本
        )
        self.base_url = base_url
        self.raw_model = (model or "").strip()
        self.model = normalize_model_name(base_url, model)
        self.last_usage = None
        logger.info(f"LLM Client initialized: model={self.model} (raw={model}), base_url={base_url}")

    async def _create(self, model: str, messages: list[dict]):
        kwargs = dict(model=model, messages=messages, temperature=0.7, max_tokens=800)
        if _is_deepseek(self.base_url, model):
            # DeepSeek V4 系列默认开启思考模式:推理 token 会吃光 max_tokens,导致正文为空。客服对话不需要长推理,显式关闭。
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        try:
            return await self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if "extra_body" in kwargs and "thinking" in str(exc).lower():      # 中转站不认识该参数:去掉重试
                kwargs.pop("extra_body")
                return await self.client.chat.completions.create(**kwargs)
            raise

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
            try:
                response = await self._create(self.model, full_messages)
            except Exception as exc:
                # 归一后的模型名被服务端判为不存在时,退回管理员填写的原名再试一次(可能是中转站自定义名)
                msg = str(exc).lower()
                if self.raw_model and self.raw_model != self.model and "model" in msg and any(
                        k in msg for k in ("not exist", "not found", "does not", "invalid model", "unknown model")):
                    logger.warning("model %s rejected, retrying with configured name %s", self.model, self.raw_model)
                    response = await self._create(self.raw_model, full_messages)
                else:
                    raise
            usage = getattr(response, "usage", None)
            self.last_usage = {
                "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "estimated": usage is None,
            }
            content = response.choices[0].message.content
            if not (content or "").strip():                      # 思考型模型可能把 token 耗在推理上而正文为空:当作失败,交给备用 AI/兜底
                raise RuntimeError("LLM returned empty content")
            return content
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise
