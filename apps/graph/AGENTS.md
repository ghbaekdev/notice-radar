# AGENTS.md

This file governs `/Users/tom/Desktop/projects/notice-radar/apps/graph` and all child paths.

## Scope

- LangGraph application and node/tool orchestration.
- Shared agent definitions, retrieval utilities, and repositories stay in `packages/core`.

## Working Rules

- Keep node behavior explicit and traceable.
- Prefer small node-local helpers over broad abstractions.
- Preserve state contract compatibility unless the task explicitly changes it.
- Keep imports relative inside `graph`, absolute when importing from `core`.

## Key References

- `docs/langgraph-server.md`
- `/Users/tom/Desktop/projects/notice-radar/docs/conventions.md`
- `/Users/tom/Desktop/projects/notice-radar/docs/architecture.md`

## Common Commands

```bash
uv sync --package rag-graph
langgraph dev
uv run ruff check apps/graph packages/core
uv run pytest apps/graph/tests
```
