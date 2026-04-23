# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

공고/문서 기반 검색 서비스를 위한 RAG 백엔드 모노레포. uv workspace로 `packages/core`, `apps/api`, `apps/graph`를 사용합니다. 하이브리드 벡터 검색(Dense+Sparse+Rerank), 멀티 LLM(OpenAI/Anthropic/Google), 멀티테넌시(company 기반 격리).

## Package Structure

```
packages/
├── core/    # 공유 라이브러리 (agents, database, shared, utils, configuration)
apps/
├── api/     # FastAPI 서버 (routers, webapp, dependencies)
└── graph/   # LangGraph 서버 (nodes, tools, graph, state, prompts)
```

## Quick Start

```bash
uv sync                                    # workspace 전체 의존성 설치
uv sync --package rag-api                  # api 패키지만

# FastAPI 서버
uv run --package rag-api python apps/api/run_api.py # FastAPI 단독 서버 (localhost:5303)

# LangGraph 서버
langgraph dev                              # LangGraph 서버 (localhost:2024)

# 린트 & 테스트
uv run ruff check apps/ packages/          # 린트
uv run ruff format apps/ packages/         # 포맷
uv run pytest                              # 테스트
```

## Key Files

### Core (공유 라이브러리)

| Path | Purpose |
|------|---------|
| `packages/core/src/core/agents/base.py` | AgentDefinition, ToolDefinition, HandoffConfig, ApiCallConfig |
| `packages/core/src/core/agents/registry.py` | AgentRegistry — 워크플로우 로딩, 에이전트 정의 캐싱 |
| `packages/core/src/core/database/` | asyncpg 커넥션 풀 + Repository 패턴 |
| `packages/core/src/core/shared/vector_search.py` | 하이브리드 검색 (Gemini dense + BM25 sparse + Cohere rerank) |
| `packages/core/src/core/shared/retrieve.py` | retrieve_documents() — 검색 파이프라인 (graph 공유) |
| `packages/core/src/core/configuration.py` | 런타임 설정 (LLM 프로바이더, 모델) |
| `packages/core/src/core/utils/llm.py` | 멀티 LLM 로더 |
| `packages/core/src/core/utils/auth.py` | JWT 인증 |

### API (FastAPI)

| Path | Purpose |
|------|---------|
| `apps/api/src/api/webapp.py` | FastAPI 앱 (lifespan, CORS, 라우터 등록) |
| `apps/api/src/api/dependencies.py` | FastAPI 의존성 주입 (get_current_company) |
| `apps/api/src/api/routers/` | 라우터 (document, auth, settings, faq, conversation, api_config, lead) |

### Graph (LangGraph)

| Path | Purpose |
|------|---------|
| `langgraph.json` | LangGraph Platform 설정 |
| `apps/graph/src/graph/graph.py` | 메인 그래프 정의 및 라우팅 |
| `apps/graph/src/graph/state.py` | State 데이터클래스 + 리듀서 |
| `apps/graph/src/graph/prompts.py` | LLM 시스템 프롬프트 |
| `apps/graph/src/graph/nodes/` | 노드 구현 (router, scope_guard, agent_generate, agent_tools, filter_output) |
| `apps/graph/src/graph/tools/` | 도구 (retrieve, handoff, api_call) |

## Import Conventions

- 공유 코드: `from core.agents import ...`, `from core.database import ...`
- API 내부: relative imports (`from .routers import ...`, `from ..dependencies import ...`)
- Graph 내부: relative imports (`from .nodes import ...`, `from ..state import ...`)
- Cross-package: absolute imports (`from core.shared.retrieve import retrieve_documents`)

## Deployment

- Blue-Green 배포 (docker-compose.blue/green.yml)
- 서비스별 독립 이미지 태그 (WEB_TAG, API_TAG, GRAPH_TAG)
- CI에서 변경된 서비스만 빌드 (dorny/paths-filter)
- packages/core/ 변경 시 api, graph 모두 리빌드

## Documentation

| 문서 | 내용 |
|------|------|
| [docs/getting-started.md](docs/getting-started.md) | 설치, 환경변수, 서버 실행 방법 |
| [docs/architecture.md](docs/architecture.md) | 디렉토리 구조, 세 서버 모델, 요청 흐름, 멀티테넌시 |
| [apps/graph/docs/langgraph-server.md](apps/graph/docs/langgraph-server.md) | 그래프 플로우, 노드, State, Configuration, Tool |
| [apps/api/docs/fastapi-server.md](apps/api/docs/fastapi-server.md) | 라우터, 인증(JWT), 의존성 주입, 문서 처리 파이프라인 |
| [docs/database.md](docs/database.md) | 테이블 스키마, Repository 패턴, 마이그레이션 |
| [docs/chatbot-defaults.md](docs/chatbot-defaults.md) | 챗봇 디폴트 가이던스 |
| [docs/conventions.md](docs/conventions.md) | 네이밍, 타이핑, 임포트, 비동기, 에러 처리, 로깅 패턴 |
| [docs/servers.md](docs/servers.md) | 서버 도메인, SSH, Blue-Green 포트, 배포, 선택적 빌드 |
