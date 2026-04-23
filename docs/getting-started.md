# Getting Started

## 요구 사항

- Python 3.13+
- `uv`
- PostgreSQL
- Qdrant

## 의존성 설치

```bash
uv sync
```

## 환경변수

- 기본값은 루트 `.env`에서 읽음
- `apps/api/.env`, `apps/graph/.env`가 있으면 해당 앱 실행 시 루트 값을 override함

### LLM

| 변수 | 설명 |
|------|------|
| `OPENAI_API_KEY` | OpenAI API 키 |
| `ANTHROPIC_API_KEY` | Anthropic API 키 |
| `GEMINI_API_KEY` | Gemini API 키 |
| `COHERE_API_KEY` | Cohere rerank 키 |

### 데이터 저장소

| 변수 | 설명 |
|------|------|
| `POSTGRES_URI` | PostgreSQL 연결 URI |
| `POSTGRES_HOST` | PostgreSQL 호스트 |
| `POSTGRES_PORT` | PostgreSQL 포트 |
| `POSTGRES_USER` | PostgreSQL 사용자 |
| `POSTGRES_PASSWORD` | PostgreSQL 비밀번호 |
| `POSTGRES_DATABASE` | PostgreSQL DB 이름 |
| `QDRANT_HOST` | Qdrant 호스트 |
| `QDRANT_PORT` | Qdrant 포트 |

### 선택

| 변수 | 설명 |
|------|------|
| `AWS_ACCESS_KEY_ID` | S3 액세스 키 |
| `AWS_SECRET_ACCESS_KEY` | S3 시크릿 키 |
| `AWS_REGION` | S3 리전 |
| `S3_BUCKET_NAME` | 파싱 결과 캐시 버킷 |
| `JWT_SECRET_KEY` | JWT 시크릿 |
| `LANGSMITH_API_KEY` | LangSmith 키 |
| `LANGSMITH_PROJECT` | LangSmith 프로젝트 |

## 실행

### LangGraph

```bash
langgraph dev
```

- `http://localhost:2024`
- `langgraph.json`의 `graph.graph:graph` 엔트리포인트 사용

### FastAPI

```bash
uv run --package rag-api python apps/api/run_api.py
```

- `http://localhost:5303`
- 문서 관리/설정/FAQ/대화 API만 별도로 띄울 때 사용

## 검증

```bash
uv run ruff check apps/ packages/
uv run python -m unittest apps.graph.tests.test_agent_tools apps.graph.tests.test_router
```
