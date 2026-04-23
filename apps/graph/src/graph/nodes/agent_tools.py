"""Agent tools node - executes the builtin retrieve tool."""

import logging

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from ..registry import AgentRegistry
from ..state import AgentState
from .trace import trace_node

logger = logging.getLogger(__name__)


async def _get_registry(config: RunnableConfig) -> AgentRegistry:
    """Get registry from config or fall back to default."""
    configurable = (config or {}).get("configurable", {})
    registry = configurable.get("_registry")
    if registry is not None:
        return registry

    return AgentRegistry.default()


async def agent_tools(state: AgentState, config: RunnableConfig) -> dict:
    """Execute the retrieve tool emitted by the chatbot model."""
    registry = await _get_registry(config)
    current_agent_id = registry.entry_agent_id
    agent_def = registry.get_entry_agent()

    last_message = state.messages[-1] if state.messages else None
    if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
        logger.warning("[agent_tools] No tool calls found in last message")
        return {}

    results = []
    trace_steps = []
    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        tool_call_id = tool_call["id"]

        logger.info(
            "[STEP: agent_tools] agent=%s | tool=%s | args=%s",
            current_agent_id,
            tool_name,
            tool_args,
        )

        try:
            if tool_name == "retrieve":
                with trace_node("agent_tools", current_agent_id, "retrieve") as step:
                    step.agent_name = agent_def.name
                    from ..tools.retrieve import retrieve_documents
                    configurable = (config or {}).get("configurable", {})
                    configurable["faq_confidence_threshold"] = registry.faq_confidence_threshold
                    query = tool_args.get("query", "")
                    result = await retrieve_documents(query, config)
                    docs = result.get("documents", [])
                    sources = result.get("sources", [])
                    summary = result.get("summary", f"Retrieved {len(docs)} documents")
                    step.tool_name = "retrieve"
                    step.tool_args = {"query": query}
                    step.tool_result = f"{len(docs)} docs, {len(sources)} sources"
                    step.metrics = result.get("metrics", {})
                    step.retrieval_details = result.get("retrieval_details", [])
                    logger.info(
                        "[STEP: agent_tools] retrieve result: %d documents, %d sources",
                        len(docs),
                        len(sources),
                    )
                result: dict = {
                    "documents": docs,
                    "sources": sources,
                    "messages": [
                        ToolMessage(content=summary, tool_call_id=tool_call_id)
                    ],
                }
                result["execution_trace"] = [step.to_dict()]
                return result

            logger.error("[agent_tools] Unsupported tool '%s'", tool_name)
            results.append(
                ToolMessage(
                    content=f"Error: unsupported tool '{tool_name}'",
                    tool_call_id=tool_call_id,
                )
            )

        except Exception as e:
            logger.error(f"[agent_tools] Error executing {tool_name}: {e}", exc_info=True)
            results.append(
                ToolMessage(
                    content=f"Error executing {tool_name}: {e!s}",
                    tool_call_id=tool_call_id,
                )
            )
            trace_steps.append(
                {
                    "node": "agent_tools",
                    "agent": current_agent_id,
                    "phase": "error",
                    "tool_name": tool_name,
                    "tool_result": str(e)[:200],
                }
            )

    if results:
        out: dict = {"messages": results}
        if trace_steps:
            out["execution_trace"] = trace_steps
        return out
    return {}
