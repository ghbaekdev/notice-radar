# Database

## 커넥션 풀

**파일**: `packages/core/src/core/database/connection.py`

- asyncpg 커넥션 풀 초기화/종료 담당
- `POSTGRES_URI` 또는 개별 접속 환경변수 사용

## 현재 사용하는 테이블

| 테이블 | 주요 컬럼 | 용도 |
|--------|-----------|------|
| `companies` | id, name, password_hash | 테넌트 |
| `company_settings` | company_id, embed_api_key, embed_allowed_domains, embed_theme_color, embed_greeting, embed_label | 임베드 설정 |
| `documents` | id, company_id, parsed_file_id, original_filename, status | 업로드 문서 메타데이터 |
| `parsed_files` | id, file_hash, s3_key, file_type, file_size | 파싱 캐시 |
| `faqs` | id, company_id, question, answer, is_active | FAQ |
| `conversations` | id, company_id, thread_id, source, message_count | 대화 세션 |
| `conversation_messages` | id, conversation_id, role, content, sources, execution_trace | 대화 메시지 |
| `api_configs` | company_id, name, endpoint, method, auth_type | 외부 API 호출 설정 |
| `lead_configs` / `leads` | company_id, capture_fields, hq_response | 리드 수집 설정 및 저장 |

## Repository 패턴

**파일**: `packages/core/src/core/database/repository.py`

| Repository | 주요 메서드 |
|-----------|------------|
| `CompanyRepository` | `get_or_create()`, `get_by_name()`, `create_with_password()`, `update_password()` |
| `DocumentRepository` | `create()`, `get_by_company()`, `get_by_id()`, `delete()`, `update_status()` |
| `ParsedFileRepository` | `get_by_hash()`, `create()`, `get_all()`, `get_by_id()` |
| `ConversationRepository` | `create_or_get()`, `add_message()`, `get_by_company()`, `get_messages()` |
| `FAQRepository` | `create()`, `get_by_company()`, `search()`, `delete()` |
| `ApiConfigRepository` | `create()`, `get_by_company()`, `get_by_id()`, `update()`, `delete()` |
| `LeadConfigRepository` | `get_by_company()`, `upsert()` |
| `LeadRepository` | `create()`, `get_by_company()` |

## 마이그레이션 메모

- 현재 코드 경로는 문서, FAQ, 대화, API config, lead 관련 스키마를 기준으로 동작
- 예전 workflow/voice 계열 테이블이 DB에 남아 있어도 현 런타임은 사용하지 않음
