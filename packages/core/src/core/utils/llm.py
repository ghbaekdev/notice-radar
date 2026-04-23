"""Utility functions for RAG agent."""

import logging
import os
from typing import Literal

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

LLMProvider = Literal["openai", "anthropic", "google"]


def load_chat_model(
    provider: LLMProvider = "google",
    model: str | None = None,
    temperature: float = 0.7,
    tags: list[str] | None = None,
    streaming: bool = True,
) -> BaseChatModel:
    """Load a chat model based on provider.

    Args:
        provider: The LLM provider (openai, anthropic, google)
        model: Model name (uses provider default if not specified)
        temperature: Model temperature
        tags: Optional tags for stream filtering (e.g., ["user-facing"] or ["internal"])
        streaming: Whether to enable streaming (disable for internal nodes to hide output)

    Returns:
        BaseChatModel instance

    Raises:
        ValueError: If provider is not supported
    """
    logger.info(
        "[load_chat_model] provider=%s | model=%s | temperature=%.1f | streaming=%s",
        provider, model, temperature, streaming,
    )

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model or "gpt-5.4-mini",
            temperature=temperature,
            api_key=os.getenv("OPENAI_API_KEY"),
            tags=tags or [],
            streaming=streaming,
        )

    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model or "claude-sonnet-4-6",
            temperature=temperature,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            tags=tags or [],
            streaming=streaming,
        )

    elif provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model or "gemini-2.5-flash",
            temperature=temperature,
            google_api_key=os.getenv("GEMINI_API_KEY"),
            tags=tags or [],
            streaming=streaming,
        )

    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")


def format_docs_as_xml(docs: list[dict]) -> list[str]:
    """Format documents as XML strings.

    Args:
        docs: List of document dicts with 'content', 'heading', etc.

    Returns:
        List of XML-formatted document strings
    """
    formatted = []
    for i, doc in enumerate(docs, 1):
        content = doc.get("content", "")
        heading = doc.get("heading", "")

        parts = [f'<document id="{i}"']
        if heading:
            parts.append(f' heading="{heading}"')
        parts.append(f'>\n{content}\n</document>')

        formatted.append("".join(parts))

    return formatted
