"""State management for RAG agent with reducers."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Annotated

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph.message import add_messages


def reduce_docs(existing: list[str], new: list[str] | None) -> list[str]:
    """Reducer for documents - replaces existing with new on each search."""
    if new is None:
        return existing
    return new


def reduce_trace(existing: list[dict], new: list[dict] | None) -> list[dict]:
    """Reducer for execution_trace - appends new trace steps.

    Supports per-turn reset: if the first element has {"__reset": True},
    previous trace is cleared and only the new steps are kept.
    """
    if new is None:
        return existing
    if new and new[0].get("__reset"):
        return [s for s in new if not s.get("__reset")]
    return existing + new


def filter_display_messages(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """Filter messages for display - keep only the LAST AI response per conversation turn.

    Only keeps:
    - HumanMessage: Always included
    - AIMessage: Only if no tool_calls, has non-empty content, and is not internal JSON
                 AND is the last AI message before the next Human message
    - ToolMessage: Excluded (internal)
    """
    import json

    # Step 1: Basic filtering (exclude tool messages, JSON patterns)
    filtered: list[AnyMessage] = []

    for msg in messages:
        # Human messages are always included
        if isinstance(msg, HumanMessage):
            filtered.append(msg)
            continue

        # AI messages: filter based on content and tool_calls
        if isinstance(msg, AIMessage):
            # Exclude if has tool_calls
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                continue

            content = str(msg.content).strip() if msg.content else ""
            if not content:
                continue

            # Exclude if content is internal JSON pattern
            if content.startswith('{') and content.endswith('}'):
                try:
                    parsed = json.loads(content)
                    # Filter internal JSON messages (intent/queries)
                    if 'intent' in parsed or 'queries' in parsed:
                        continue
                except json.JSONDecodeError:
                    pass

            filtered.append(msg)

    # Step 2: Keep only the LAST AI message per conversation turn
    # Human1 → AI1_v1 → AI1_v2 → Human2 → AI2 => Human1 → AI1_v2 → Human2 → AI2
    result: list[AnyMessage] = []
    i = 0
    while i < len(filtered):
        msg = filtered[i]

        if isinstance(msg, HumanMessage):
            result.append(msg)
            # Find the last AI message before the next Human
            j = i + 1
            last_ai_idx = -1
            while j < len(filtered) and not isinstance(filtered[j], HumanMessage):
                if isinstance(filtered[j], AIMessage):
                    last_ai_idx = j
                j += 1

            if last_ai_idx >= 0:
                result.append(filtered[last_ai_idx])
            i = j  # Move to next Human
        else:
            # Orphan AI message at the start (no preceding Human)
            i += 1

    return result


@dataclass
class InputState:
    """Input state for the agent - what comes from outside."""
    messages: Annotated[Sequence[AnyMessage], add_messages]
    company: str = "wiseai"


@dataclass
class AgentState(InputState):
    """Full agent state with internal fields."""
    documents: Annotated[list[str], reduce_docs] = field(default_factory=list)
    sources: Annotated[list[dict], reduce_docs] = field(default_factory=list)
    is_last_step: bool = False
    current_agent: str = ""
    execution_trace: Annotated[list[dict], reduce_trace] = field(default_factory=list)


@dataclass
class OutputState:
    """Output state - what gets returned to caller.

    Returns the full messages list - filtering should be done client-side
    if needed, since LangGraph requires consistent reducers for the same channel.
    """
    messages: Annotated[Sequence[AnyMessage], add_messages]
    execution_trace: Annotated[list[dict], reduce_trace] = field(default_factory=list)
