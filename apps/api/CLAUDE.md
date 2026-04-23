# CLAUDE.md

This file provides guidance for work in `/Users/tom/Desktop/projects/notice-radar/apps/api`.

## Focus

- FastAPI document API only.
- Routers live under `src/routers`.
- Application setup lives in `src/webapp.py`.
- Auth dependency wiring lives in `src/dependencies.py`.

## Package Structure

```text
apps/api/
├── src/
│   ├── webapp.py
│   ├── dependencies.py
│   └── routers/
├── tests/
└── docs/
```

## Conventions

- Keep API-layer imports relative.
- Return HTTP-friendly errors with `HTTPException`.
- Reuse `core.database` repositories and `core.shared` retrieval/parsing utilities rather than duplicating logic in routers.
- When adding parsing or indexing behavior, verify both cache behavior and retrieval implications.

## Verification

```bash
uv run ruff check apps/api packages/core
uv run pytest apps/api/tests
```
