# 챗봇 디폴트 가이던스

## 시스템 프롬프트

Source: `apps/graph/src/prompts.py`

- 문서/FAQ 기반으로만 답변
- 일반 잡담이 아니면 `retrieve` 우선 사용
- 문서에 없으면 없다고 답변
- 사용자 언어를 따름
- 응답은 간결한 마크다운

## 기본 에이전트

Source: `apps/graph/src/registry.py`

| 설정 | 값 |
|------|----|
| `entry_agent` | `info_agent` |
| `llm_provider` | `openai` |
| `llm_model` | `gpt-5.4-mini` |
| `faq_confidence_threshold` | `0.7` |

```json
{
  "id": "info_agent",
  "name": "정보 안내",
  "instructions": "회사의 문서와 FAQ를 기반으로 고객 질문에 답변합니다."
}
```

## 검색 관련 기본값

Source: `packages/core/src/core/configuration.py`

| 설정 | 기본값 |
|------|--------|
| `retrieval_limit` | `5` |
| `query_rewrite_enabled` | `True` |
| `query_rewrite_model` | `gpt-4o-mini` |
| `faq_enabled` | `True` |
| `faq_confidence_threshold` | `0.7` |
