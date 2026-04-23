# Notice Radar

문서 파싱, 하이브리드 검색, FAQ 응답, 대화 로그 저장에 집중한 백엔드 모노레포입니다. `livekit-agent-builder`에서 시작했지만 현재 워크스페이스는 `voice`와 멀티-agent workflow 표면을 제거한 단일 RAG 챗봇 기준으로 정리되어 있습니다.

## 패키지 구조

| 패키지 | 역할 |
|--------|------|
| `packages/core` | 공유 로직: DB, 검색, 인증, 설정 |
| `apps/api` | FastAPI 문서 파싱/검색 API |
| `apps/graph` | LangGraph 기반 RAG 챗봇 런타임 및 단일-agent 레지스트리 |

## 그래프 플로우

```text
START → router → agent_generate → (agent_tools or filter_output) → END
```

- `router`: 단일 `info_agent` 레지스트리 초기화
- `agent_generate`: 검색 필요 여부 판단 또는 최종 답변 생성
- `agent_tools`: builtin `retrieve` 실행
- `filter_output`: 내부 메시지 제거 후 대화 로그 저장

## 주요 기능

- Dense + Sparse + Cohere rerank 기반 하이브리드 검색
- 회사별 문서/FAQ/대화 데이터 분리
- PDF 파싱, 청킹, 임베딩, Qdrant 인덱싱
- LangGraph 기반 단일-agent RAG 응답
- FastAPI 기반 문서 업로드/검색 API

## 실행

```bash
uv sync
langgraph dev
uv run --package rag-api python apps/api/run_api.py
```

## 검증

```bash
uv run ruff check apps/ packages/
uv run python -m unittest apps.graph.tests.test_agent_tools apps.graph.tests.test_router
```
