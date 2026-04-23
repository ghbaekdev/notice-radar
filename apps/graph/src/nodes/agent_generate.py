"""Agent generate node for the single-agent RAG chatbot."""

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from core.configuration import Configuration
from core.utils import load_chat_model
from prompts import format_system_prompt
from registry import AgentRegistry
from state import AgentState

from .trace import trace_node

logger = logging.getLogger(__name__)


async def _get_registry(config: RunnableConfig) -> AgentRegistry:
    """Get registry from config or fall back to default."""
    configurable = (config or {}).get("configurable", {})
    registry = configurable.get("_registry")
    if registry is not None:
        return registry

    return AgentRegistry.default()


async def agent_generate(state: AgentState, config: RunnableConfig) -> dict:
    """Generate a retrieve decision or a final answer for the chatbot."""
    configuration = Configuration.from_runnable_config(config)
    registry = await _get_registry(config)

    current_agent_id = registry.entry_agent_id
    agent_def = registry.get_entry_agent()

    has_retrieve_been_called = False
    for msg in reversed(state.messages):
        if isinstance(msg, HumanMessage):
            break
        if isinstance(msg, AIMessage) and msg.tool_calls:
            if any(tc["name"] == "retrieve" for tc in msg.tool_calls):
                has_retrieve_been_called = True
            break

    is_respond_phase = bool(state.documents) or has_retrieve_been_called

    phase = "respond" if is_respond_phase else "retrieve"
    all_tools = []
    if not is_respond_phase:
        from tools import tools as builtin_tools
        all_tools.extend(builtin_tools)

    if is_respond_phase:
        context = "\n\n".join(state.documents)
        system_content = format_system_prompt(context=context, with_tools=False)
    else:
        system_content = format_system_prompt(context=None, with_tools=True)

    with trace_node("agent_generate", current_agent_id, phase) as step:
        step.agent_name = agent_def.name if agent_def else current_agent_id
        llm_provider = registry.llm_provider
        llm_model = registry.llm_model
        model = load_chat_model(
            provider=llm_provider,
            model=llm_model,
            temperature=configuration.llm_temperature,
        )

        tool_names = [getattr(t, "name", str(t)) for t in all_tools]
        prompt_preview = system_content[:120].replace("\n", " ")
        logger.info(
            "[STEP: agent_generate] agent=%s | phase=%s | llm=%s/%s\n"
            "  tools: %s\n"
            "  prompt: %s...",
            current_agent_id,
            phase,
            llm_provider,
            llm_model,
            tool_names or "(none)",
            prompt_preview,
        )

        if all_tools:
            model = load_chat_model(
                provider=llm_provider,
                model=llm_model,
                temperature=configuration.llm_temperature,
                tags=["internal"],
                streaming=False,
            )
            model_with_tools = model.bind_tools(all_tools)
        else:
            model_with_tools = model

        system_msg = SystemMessage(content=system_content)

        if is_respond_phase:
            filtered_messages = []
            for msg in state.messages:
                if isinstance(msg, AIMessage) and msg.tool_calls:
                    continue
                if hasattr(msg, "type") and msg.type == "tool":
                    continue
                filtered_messages.append(msg)
            messages = [system_msg, *filtered_messages]
        else:
            messages = [system_msg, *list(state.messages)]

        response = await model_with_tools.ainvoke(messages)

        content_len = len(str(response.content)) if response.content else 0
        tc_names = [tc["name"] for tc in response.tool_calls] if response.tool_calls else []
        logger.info(
            "[STEP: agent_generate] response: %d chars | tool_calls=%s",
            content_len,
            tc_names if tc_names else "none",
        )

        if tc_names:
            step.tool_name = ",".join(tc_names)
        step.tool_result = f"{content_len} chars"

        if response.tool_calls:
            response.content = ""

        if state.sources and not response.tool_calls:
            response_text = str(response.content)
            if "찾을 수 없습니다" not in response_text:
                response.additional_kwargs["sources"] = state.sources

    return {"messages": [response], "execution_trace": [step.to_dict()]}
