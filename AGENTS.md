# AGENTS.md

This file governs `/Users/tom/Desktop/projects/notice-radar` and all child paths.

## Project Intent

- Notice Radar backend monorepo for document parsing, retrieval, and graph-based chat flows.
- Keep the uv workspace split intact: `packages/core` for shared logic, `apps/api` for FastAPI, `apps/graph` for LangGraph.
- Prefer the smallest correct change in the correct package. Shared behavior belongs in `packages/core`.

## Package Map

- `packages/core`: shared agents, database, retrieval, auth, configuration, utilities
- `apps/api`: FastAPI app, routers, dependencies, web surface
- `apps/graph`: LangGraph state, nodes, tools, prompts

## Working Rules

- Preserve multi-tenant boundaries. Company-scoped isolation and auth assumptions are core behavior, not incidental details.
- Reuse existing repository, handler, and factory patterns before introducing new abstractions.
- Keep cross-package imports absolute, for example `from core.shared.retrieve import ...`.
- Keep package-internal imports relative inside `api` and `graph`.
- Follow existing async, typing, logging, and naming conventions from `docs/conventions.md`.
- Do not add new dependencies or new service boundaries unless explicitly requested.

## Key References

- `docs/getting-started.md`
- `docs/architecture.md`
- `docs/database.md`
- `docs/conventions.md`
- `apps/api/docs/fastapi-server.md`
- `apps/graph/docs/langgraph-server.md`

## Common Commands

```bash
uv sync
uv sync --package rag-api
uv run --package rag-api python apps/api/run_api.py
langgraph dev
uv run ruff check apps/ packages/
uv run ruff format apps/ packages/
uv run pytest
```

## Verification

- Run the smallest relevant check first for the package you changed.
- For non-trivial changes, finish with `uv run ruff check apps/ packages/` and `uv run pytest`.
- If runtime behavior changed, run the nearest relevant startup command and report any gaps you could not verify.
