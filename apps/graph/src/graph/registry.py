"""Single-agent registry for the graph runtime."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentProfile:
    id: str
    name: str
    instructions: str


class AgentRegistry:
    """Holds the one chatbot agent profile still used by the graph."""

    def __init__(
        self,
        *,
        llm_provider: str = "openai",
        llm_model: str = "gpt-5.4-mini",
        faq_confidence_threshold: float = 0.7,
        greeting_message: str = "",
    ) -> None:
        self.entry_agent_id = "info_agent"
        self.greeting_message = greeting_message
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.faq_confidence_threshold = faq_confidence_threshold
        self._entry_agent = AgentProfile(
            id="info_agent",
            name="정보 안내",
            instructions="회사의 문서와 FAQ를 기반으로 고객 질문에 답변합니다.",
        )

    def get_agent(self, agent_id: str) -> AgentProfile | None:
        if agent_id == self.entry_agent_id:
            return self._entry_agent
        return None

    def get_entry_agent(self) -> AgentProfile:
        return self._entry_agent

    def get_all_agents(self) -> dict[str, AgentProfile]:
        return {self.entry_agent_id: self._entry_agent}

    @classmethod
    def default(cls) -> "AgentRegistry":
        return cls()
