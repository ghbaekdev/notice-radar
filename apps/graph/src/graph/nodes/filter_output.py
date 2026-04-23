"""Filter output node - removes internal messages before output."""

import logging

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from ..state import AgentState, filter_display_messages
from .trace import trace_node

logger = logging.getLogger(__name__)


async def filter_output(state: AgentState, config: RunnableConfig) -> dict:
    """Filter messages before output to client.

    Removes:
    - Tool calls and tool results
    - Internal JSON messages (intent/queries)

    Also marks the final AI response with user_facing=true for frontend filtering.
    Then saves conversation log to database.
    """
    with trace_node("filter_output", state.current_agent or "", "complete") as step:
        # Resolve agent name from registry
        configurable = (config or {}).get("configurable", {})
        _registry = configurable.get("_registry")
        if _registry and state.current_agent:
            _agent_def = _registry.get_agent(state.current_agent)
            step.agent_name = _agent_def.name if _agent_def else state.current_agent or ""
        filtered = filter_display_messages(state.messages)

        # Mark the final AI response with user_facing=true
        # This allows frontend to filter and display only the final response
        response_len = 0
        sources_count = 0
        for msg in reversed(filtered):
            if isinstance(msg, AIMessage) and msg.content:
                msg.additional_kwargs["user_facing"] = True
                response_len = len(str(msg.content))
                sources_count = len(msg.additional_kwargs.get("sources", []))
                break

        step.tool_result = f"{response_len} chars, {sources_count} sources"

        logger.info(
            "──────────────────────────────────────────────────────────\n"
            "[PIPELINE END] agent=%s | response=%d chars | sources=%d\n"
            "══════════════════════════════════════════════════════════",
            state.current_agent or "?",
            response_len,
            sources_count,
        )

        # Build full execution trace for this turn (state has accumulated steps, plus this final step)
        full_trace = [*state.execution_trace, step.to_dict()]

        # Save conversation log (non-blocking, errors don't affect chat response)
        try:
            await _save_conversation_log(state, config, filtered, full_trace)
        except Exception as e:
            logger.error(f"[filter_output] Failed to save conversation log: {e}", exc_info=True)

    result: dict = {"messages": filtered}
    configurable = (config or {}).get("configurable", {})
    if configurable.get("enable_trace", False):
        result["execution_trace"] = [step.to_dict()]
    return result


async def _save_conversation_log(state: AgentState, config: RunnableConfig, filtered: list, execution_trace: list[dict] | None = None) -> None:
    """Save conversation messages to database."""
    configurable = (config or {}).get("configurable", {})
    thread_id = configurable.get("thread_id")
    if not thread_id:
        return  # Skip if no thread_id (running outside LangGraph Platform)

    from core.configuration import Configuration
    from core.database.repository import CompanyRepository, ConversationRepository

    cfg = Configuration.from_runnable_config(config)
    company = await CompanyRepository().get_by_name(cfg.company)
    if not company:
        return

    source = configurable.get("source", "embed")
    repo = ConversationRepository()
    conv = await repo.create_or_get(company["id"], thread_id, source)

    # Save only new messages (compare with existing message_count)
    existing_count = conv["message_count"]
    new_messages = filtered[existing_count:]

    for msg in new_messages:
        if isinstance(msg, HumanMessage):
            await repo.add_message(conv["id"], "human", str(msg.content))
        elif isinstance(msg, AIMessage) and msg.content:
            sources = msg.additional_kwargs.get("sources", [])
            if state.current_agent and sources:
                sources = [{"agent": state.current_agent}, *sources]
            await repo.add_message(conv["id"], "ai", str(msg.content), sources, execution_trace)
