"""
LLM Client — 统一接口，内部走 services.llm_gateway（公司私有大模型，OpenAI 兼容）。
"""
import os
import logging
from typing import Optional

from services.llm_gateway import get_anthropic_compatible_client

logger = logging.getLogger(__name__)

# Singleton client
_client: Optional["LLMClient"] = None


class LLMClient:
    """Wrapper around Anthropic SDK configured for MiniMax M2.7."""

    DEFAULT_MODEL = "default"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.model = model or os.getenv("LLM_MODEL", self.DEFAULT_MODEL)
        # 统一走私有大模型网关（OpenAI 兼容）；api_key/base_url 由 llm_gateway 管理。
        self.client = get_anthropic_compatible_client()
        logger.info("LLMClient initialized via llm_gateway, model=%s", self.model)

    def chat(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """
        Send a single message and get a text response.

        Args:
            prompt: User message
            system: System prompt
            max_tokens: Max output tokens
            temperature: Sampling temperature

        Returns:
            Model's text response
        """
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        response = self.client.messages.create(**kwargs)
        return _extract_text(response)

    def chat_with_messages(
        self,
        messages: list,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """
        Multi-turn conversation.

        Args:
            messages: List of {"role": ..., "content": ...} dicts
            system: System prompt
            max_tokens: Max output tokens
            temperature: Sampling temperature

        Returns:
            Model's text response
        """
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        response = self.client.messages.create(**kwargs)
        return _extract_text(response)


def _extract_text(response) -> str:
    """Extract text from response, skipping ThinkingBlocks."""
    content = getattr(response, "content", None) or []
    for block in content:
        block_type = getattr(block, "type", None)
        # Skip thinking blocks explicitly
        if block_type == "thinking":
            continue
        if block_type == "text" and hasattr(block, "text"):
            return block.text
    # Second pass: any block with text attribute that isn't a ThinkingBlock
    for block in content:
        if hasattr(block, "text") and not hasattr(block, "thinking") and not hasattr(block, "signature"):
            return block.text
    # Fallback: try to get any string content
    if content:
        last = content[-1]
        if hasattr(last, "text"):
            return last.text
        return str(last)
    raise ValueError("No text content in response")


def get_llm_client() -> LLMClient:
    """Get or create singleton LLM client."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
