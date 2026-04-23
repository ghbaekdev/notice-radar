"""Configuration management for RAG agent."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Literal

from langchain_core.runnables import RunnableConfig

LLMProvider = Literal["openai", "anthropic", "google"]


@dataclass(kw_only=True)
class Configuration:
    """Configuration for the RAG agent.

    This class can be used to configure the agent at runtime via
    LangGraph Studio or programmatically through RunnableConfig.
    """

    # LLM Configuration
    llm_provider: LLMProvider = "openai"
    llm_model: str = "gpt-5.4-mini"
    llm_temperature: float = 0.7

    # Company/Tenant Configuration
    company: str = "wiseai"

    # Retrieval Configuration
    retrieval_limit: int = 5

    # Query Rewriting Configuration
    query_rewrite_enabled: bool = True  # Whether to use multi-query rewriting
    query_rewrite_model: str = "gpt-5.4-nano"  # Model for query rewriting (should be fast/cheap)

    # FAQ Configuration
    faq_enabled: bool = True  # Whether to search FAQs before documents
    faq_confidence_threshold: float = 0.7  # Score threshold for direct FAQ response

    # Parent Document Retrieval
    parent_context_enabled: bool = True  # Fetch parent chunk for section-level context

    # Response Configuration
    response_language: str | None = None  # None = auto-detect from user message

    # Trace Configuration
    enable_trace: bool = False  # Whether to collect execution trace steps

    @classmethod
    def from_runnable_config(
        cls, config: RunnableConfig | None = None
    ) -> Configuration:
        """Create Configuration from a RunnableConfig.

        Args:
            config: LangGraph RunnableConfig containing configurable fields

        Returns:
            Configuration instance with values from config or defaults
        """
        configurable = (config or {}).get("configurable", {})

        # Get all field names from the dataclass
        field_names = {f.name for f in fields(cls)}

        # Extract matching values from configurable
        values = {
            k: v for k, v in configurable.items()
            if k in field_names and v is not None
        }

        return cls(**values)
