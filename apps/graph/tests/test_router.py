from unittest import IsolatedAsyncioTestCase

from nodes.router import router
from state import AgentState


class RouterTest(IsolatedAsyncioTestCase):
    async def test_router_initializes_info_agent_and_resets_trace(self) -> None:
        result = await router(AgentState(messages=[]), {"configurable": {}})

        self.assertEqual(result["current_agent"], "info_agent")
        self.assertEqual(result["documents"], [])
        self.assertEqual(result["sources"], [])
        self.assertEqual(len(result["execution_trace"]), 2)
        self.assertTrue(result["execution_trace"][0]["__reset"])
        self.assertEqual(result["execution_trace"][1]["node"], "router")
