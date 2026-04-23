"""Execution trace utilities for graph execution visibility."""

import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime


@dataclass
class TraceStep:
    """Single execution step in the graph trace."""
    node: str = ""
    agent: str = ""
    phase: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: str = ""
    target_agent: str = ""
    agent_name: str = ""
    duration_ms: int = 0
    timestamp: str = ""
    metrics: dict = field(default_factory=dict)
    retrieval_details: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        if len(d["tool_result"]) > 200:
            d["tool_result"] = d["tool_result"][:197] + "..."
        if not d["retrieval_details"]:
            del d["retrieval_details"]
        return d


@contextmanager
def trace_node(node: str, agent: str, phase: str):
    """Context manager that measures duration and yields a TraceStep to populate."""
    step = TraceStep(
        node=node,
        agent=agent,
        phase=phase,
        timestamp=datetime.now(UTC).isoformat(),
    )
    start = time.monotonic()
    yield step
    step.duration_ms = int((time.monotonic() - start) * 1000)
