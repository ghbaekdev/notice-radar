# CLAUDE.md

This file provides guidance for work in `/Users/tom/Desktop/projects/notice-radar/apps/graph`.

## Focus

- LangGraph graph definition, nodes, tools, prompts, and state.
- Runtime configuration should remain compatible with `langgraph.json`.

## Package Structure

```text
apps/graph/
├── src/
│   ├── graph.py
│   ├── state.py
│   ├── prompts.py
│   ├── nodes/
│   └── tools/
├── tests/
└── docs/
```

## Conventions

- Node logic should be deterministic where possible and observable through execution trace.
- State changes should remain minimal and explicit.
- Tool wrappers in `graph` should delegate to `core` rather than reimplement retrieval or DB logic.

## Verification

```bash
uv run ruff check apps/graph packages/core
uv run pytest apps/graph/tests
```
