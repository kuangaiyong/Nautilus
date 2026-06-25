"""
统一 LLM 网关 — 全平台所有 LLM 调用的唯一出口，指向公司私有部署的大模型。

私有模型为 OpenAI 兼容协议（/v1/chat/completions）。本模块用 openai SDK 访问，
并额外暴露一个 Anthropic 风格的 messages.create shim，使历史上散落的
`anthropic.Anthropic().messages.create(...)` 调用点可以零逻辑改动地切换过来。

配置（.env.private）：
    LLM_BASE_URL   私有模型 OpenAI 兼容端点，如 http://llm.internal:8000/v1
    LLM_API_KEY    API Key（私有网关若不校验可留任意值）
    LLM_MODEL      模型名

调用风格：
    1. from services.llm_gateway import chat
       text = chat(prompt, system=..., max_tokens=..., temperature=...)
    2. from services.llm_gateway import get_anthropic_compatible_client
       client = get_anthropic_compatible_client()
       msg = client.messages.create(model=..., max_tokens=..., system=..., messages=[...])
       text = msg.content[0].text
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_client = None  # openai.OpenAI 单例


def _get_openai_client():
    """Lazy-init OpenAI 兼容客户端，指向私有模型端点。"""
    global _client
    if _client is None:
        from openai import OpenAI

        base_url = os.getenv("LLM_BASE_URL")
        api_key = os.getenv("LLM_API_KEY") or "not-needed"
        if not base_url:
            raise ValueError(
                "LLM_BASE_URL 未配置 — 请在 .env.private 设置私有大模型 OpenAI 兼容端点"
            )
        _client = OpenAI(base_url=base_url, api_key=api_key)
        logger.info("LLM gateway initialized: base_url=%s model=%s", base_url, get_model())
    return _client


def get_model() -> str:
    return os.getenv("LLM_MODEL", "default")


def is_configured() -> bool:
    return bool(os.getenv("LLM_BASE_URL"))


def chat(
    prompt: str,
    system: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.3,
) -> str:
    """同步文本对话，返回模型回复文本。"""
    client = _get_openai_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(
        model=get_model(),
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Anthropic 兼容 shim：暴露 .messages.create(...) 与 .content[0].text 结构，
# 内部转译为 OpenAI 协议，统一打到私有模型。
# ---------------------------------------------------------------------------

class _TextBlock:
    __slots__ = ("text", "type")

    def __init__(self, text: str):
        self.text = text
        self.type = "text"


class _Message:
    """模拟 anthropic Message：msg.content[0].text

    额外暴露 usage/stop_reason，兼容形如
    `response.usage.input_tokens` / `response.stop_reason == "tool_use"`
    的 anthropic 调用点（如 agent_executor._call_llm），避免 AttributeError。
    私有模型走 OpenAI 协议、不返回 tool_use，故 stop_reason 固定为 end_turn、
    usage 置 None（调用点对 None 已做 falsy 兜底）。
    """

    def __init__(self, text: str):
        self.content = [_TextBlock(text)]
        self.usage = None
        self.stop_reason = "end_turn"


class _Messages:
    def create(
        self,
        model=None,
        max_tokens: int = 1024,
        messages=None,
        system: Optional[str] = None,
        temperature: float = 0.3,
        **kwargs,
    ):
        client = _get_openai_client()
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        for m in messages or []:
            content = m.get("content", "")
            # anthropic 允许 content 为 block 列表；这里归一为纯文本
            if isinstance(content, list):
                content = " ".join(
                    (b.get("text", "") if isinstance(b, dict) else str(b)) for b in content
                )
            oai_messages.append({"role": m.get("role", "user"), "content": content})
        resp = client.chat.completions.create(
            model=get_model(),  # 忽略传入的 anthropic 模型名，统一用私有模型
            messages=oai_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return _Message(resp.choices[0].message.content or "")


class AnthropicCompatClient:
    """暴露 .messages.create(...)，用于替换 anthropic.Anthropic() 调用点。"""

    def __init__(self, *args, **kwargs):
        self.messages = _Messages()


def get_anthropic_compatible_client(*args, **kwargs) -> AnthropicCompatClient:
    """返回 anthropic 风格客户端（内部走私有模型）。参数被忽略。"""
    return AnthropicCompatClient()
