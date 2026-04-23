# AGENTS.md

This file governs `/Users/tom/Desktop/projects/notice-radar/apps/api` and all child paths.

## Scope

- FastAPI application for document parsing, indexing, search, and document-management endpoints.
- Keep HTTP concerns in `src`; shared retrieval, database, and auth logic belongs in `packages/core`.

## Working Rules

- Prefer thin routers: validation and HTTP translation here, business logic in `core` when reused elsewhere.
- Keep package-internal imports consistent with the flat `src` layout.
- Preserve current document pipeline behavior unless the task explicitly changes it.
- Avoid adding route-specific abstractions when a small helper function is enough.

## Key References

- `docs/fastapi-server.md`
- `/Users/tom/Desktop/projects/notice-radar/docs/database.md`
- `/Users/tom/Desktop/projects/notice-radar/docs/conventions.md`

## Common Commands

```bash
uv sync --package rag-api
uv run --package rag-api python apps/api/run_api.py
uv run ruff check apps/api packages/core
uv run pytest apps/api/tests
```
