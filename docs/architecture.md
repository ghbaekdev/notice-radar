# Architecture

## 디렉토리 구조

```text
notice-radar/
├── apps/
│   ├── api/
│   │   ├── docs/
│   │   ├── src/api/
│   │   │   ├── routers/
│   │   │   ├── dependencies.py
│   │   │   └── webapp.py
│   │   └── tests/
│   └── graph/
│       ├── docs/
│       ├── src/graph/
│       │   ├── nodes/
│       │   ├── registry.py
│       │   ├── tools/
│       │   ├── graph.py
│       │   ├── prompts.py
│       │   └── state.py
│       └── tests/
├── packages/
│   └── core/
│       └── src/core/
│           ├── agents/
│           ├── database/
│           ├── shared/
│           ├── utils/
│           └── configuration.py
├── docs/
├── langgraph.json
├── pyproject.toml
└── uv.lock
```

## 런타임 구성

### 1. LangGraph 서버

- 단일 `info_agent` 기반 RAG 챗봇 런타임
- `langgraph dev`로 실행
- 그래프는 `apps/graph/src/graph/graph.py`에 정의

### 2. FastAPI 서버

- 인증, 문서 업로드/관리, FAQ, 설정, 대화 조회 API 제공
- `uv run --package rag-api python apps/api/run_api.py`로 실행 가능

## 그래프 요청 흐름

```text
Frontend / Embed
    │
    └─→ LangGraph
            │
            ├─→ router
            ├─→ agent_generate
            │       ├─→ retrieve tool call
            │       └─→ direct response
            ├─→ agent_tools
            │       └─→ core.shared.retrieve
            └─→ filter_output
                    └─→ conversations / conversation_messages 저장
```

## API 요청 흐름

```text
Client
    │
    └─→ FastAPI
            ├─→ auth / settings / faq / conversations / api_config / lead
            ├─→ document parsing + indexing
            ├─→ PostgreSQL
            ├─→ Qdrant
            └─→ S3 parsed-file cache
```

## 데이터 경계

- 멀티테넌시는 `company_id`와 회사별 Qdrant 컬렉션으로 유지
- 대화 로그에는 `sources`와 `execution_trace`를 함께 저장
- 현재 워크스페이스에는 voice 런타임과 workflow 관리 레이어가 포함되지 않음
