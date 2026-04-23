from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from nodes.agent_tools import agent_tools
from state import AgentState


class AgentToolsTest(IsolatedAsyncioTestCase):
    async def test_retrieve_tool_updates_documents_sources_and_trace(self) -> None:
        state = AgentState(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "retrieve",
                            "args": {"query": "환불 정책"},
                            "id": "tool-call-1",
                        }
                    ],
                )
            ]
        )
        config = {"configurable": {}}

        with patch(
            "tools.retrieve.retrieve_documents",
            AsyncMock(
                return_value={
                    "documents": ["doc-1"],
                    "sources": [{"title": "FAQ"}],
                    "summary": "Retrieved 1 document",
                    "metrics": {"faq_hits": 1},
                }
            ),
        ):
            result = await agent_tools(state, config)

        self.assertEqual(result["documents"], ["doc-1"])
        self.assertEqual(result["sources"], [{"title": "FAQ"}])
        self.assertEqual(len(result["messages"]), 1)
        self.assertIsInstance(result["messages"][0], ToolMessage)
        self.assertEqual(result["messages"][0].content, "Retrieved 1 document")
        self.assertEqual(len(result["execution_trace"]), 1)
        self.assertEqual(result["execution_trace"][0]["tool_name"], "retrieve")
