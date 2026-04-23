"""LangGraph flow for the single-agent RAG chatbot."""

from typing import Literal

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from nodes.agent_generate import agent_generate
from nodes.agent_tools import agent_tools
from nodes.filter_output import filter_output
from nodes.router import router
from state import AgentState, InputState, OutputState


def agent_condition(state: AgentState) -> Literal["agent_tools", "filter_output"]:
    """Route based on whether the last message has tool calls."""
    messages = state.messages
    if not messages:
        return "filter_output"

    last_message = messages[-1]

    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "agent_tools"

    return "filter_output"


# Build the graph
graph_builder = StateGraph(AgentState, input=InputState, output=OutputState)

# Add nodes
graph_builder.add_node("router", router)
graph_builder.add_node("agent_generate", agent_generate)
graph_builder.add_node("agent_tools", agent_tools)
graph_builder.add_node("filter_output", filter_output)

# Add edges
graph_builder.add_edge(START, "router")
graph_builder.add_edge("router", "agent_generate")

graph_builder.add_conditional_edges(
    "agent_generate",
    agent_condition,
    {"agent_tools": "agent_tools", "filter_output": "filter_output"},
)

# agent_tools loops back to agent_generate after retrieve updates state.
graph_builder.add_edge("agent_tools", "agent_generate")
graph_builder.add_edge("filter_output", END)

# Compile graph
graph = graph_builder.compile()
