# Code Conventions

## 네이밍

| 대상 | 규칙 |
|------|------|
| 라우터 | `router = APIRouter(prefix=..., tags=[...])` |
| Repository | `{Entity}Repository` |
| 그래프 노드 | `async def snake_case(state, config) -> dict` |
| Pydantic 모델 | `PascalCase` |

## 임포트

- 표준 라이브러리 → 서드파티 → 로컬 순서
- `core`는 절대 임포트
- `api`, `graph` 내부는 상대 임포트

## 비동기 패턴

### DB

```python
pool = get_db_pool()
async with pool.acquire() as conn:
    row = await conn.fetchrow("SELECT ...", value)
```

### LLM

```python
response = await model.ainvoke(messages)
```

## LangGraph 상태 업데이트

노드는 partial dict를 반환한다.

```python
async def node(state: AgentState, config: RunnableConfig) -> dict:
    return {"messages": [response], "documents": docs}
```

- `messages`: `add_messages` reducer로 누적
- `documents`, `sources`: 새 검색 결과로 교체
- `execution_trace`: 턴 단위 추적 정보 누적

## 메시지 필터링

- `ToolMessage`는 사용자 응답에서 제외
- 내부 JSON 패턴은 제외
- 한 턴당 마지막 AI 응답만 유지

## 설정 접근

```python
cfg = Configuration.from_runnable_config(config)
model = load_chat_model(
    provider=cfg.llm_provider,
    model=cfg.llm_model,
    temperature=cfg.llm_temperature,
)
```

## 로깅

```python
logger = logging.getLogger(__name__)
logger.info("[component] message")
logger.error("[component] error")
```

- 내부 tool-calling 단계는 `tags=["internal"]`, `streaming=False`를 사용해 프런트 스트림에 노출하지 않음
