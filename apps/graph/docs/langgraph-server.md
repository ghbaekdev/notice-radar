# LangGraph Server

## 그래프 플로우

```text
START → router → agent_generate → (agent_tools or filter_output) → END
```

## 노드

### router

- 단일 `info_agent` 레지스트리 준비
- trace reset
- `current_agent`, `documents`, `sources` 초기화

### agent_generate

- 첫 단계에서는 `retrieve` 도구를 바인딩한 모델 호출
- 검색 결과가 있으면 응답 프롬프트로 재호출
- 최종 응답에 `sources` 메타데이터 부착

### agent_tools

- 현재는 builtin `retrieve` 하나만 실행
- `core.shared.retrieve.retrieve_documents()` 호출
- 검색 결과와 trace를 상태에 반영

### filter_output

- 내부 메시지 제거
- 사용자에게 보여줄 마지막 AI 응답만 남김
- conversation log 저장

## 상태

### InputState

```python
messages: Annotated[Sequence[AnyMessage], add_messages]
company: str = "wiseai"
```

### AgentState

```python
documents: list[str]
sources: list[dict]
current_agent: str
execution_trace: list[dict]
```
