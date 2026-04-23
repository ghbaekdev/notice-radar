"""Router node - loads default registry and sets the entry agent."""

import logging

from langchain_core.runnables import RunnableConfig

from core.configuration import Configuration
from registry import AgentRegistry
from state import AgentState

from .trace import trace_node

logger = logging.getLogger(__name__)


async def router(state: AgentState, config: RunnableConfig) -> dict:
    """Load default registry configuration and set the entry agent."""

    with trace_node("router", "", "entry") as step:
        configuration = Configuration.from_runnable_config(config)
        configurable = (config or {}).get("configurable", {})
        thread_id = configurable.get("thread_id", "?")

        registry = AgentRegistry.default()

        entry_agent_id = registry.entry_agent_id
        current_agent = state.current_agent or entry_agent_id
        step.agent = current_agent
        entry_def = registry.get_agent(current_agent)
        step.agent_name = entry_def.name if entry_def else current_agent

        agent_ids = list(registry.get_all_agents().keys())

        logger.info(
            "\n══════════════════════════════════════════════════════════\n"
            "[PIPELINE START] thread=%s | company=%s\n"
            "  llm: %s/%s\n"
            "  agents: %s | entry: %s | current: %s\n"
            "──────────────────────────────────────────────────────────",
            thread_id,
            configuration.company,
            registry.llm_provider,
            registry.llm_model,
            agent_ids,
            entry_agent_id,
            current_agent,
        )

        # Store registry in config for downstream nodes to access
        configurable["_registry"] = registry
        configurable["llm_provider"] = registry.llm_provider
        configurable["llm_model"] = registry.llm_model
        configurable["faq_confidence_threshold"] = registry.faq_confidence_threshold

    # Reset sentinel clears previous turn's trace; always include step for DB persistence
    trace_reset: list[dict] = [{"__reset": True}, step.to_dict()]

    return {
        "current_agent": current_agent,
        "documents": [],
        "sources": [],
        "execution_trace": trace_reset,
    }
