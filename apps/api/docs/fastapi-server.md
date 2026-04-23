# FastAPI Server

## 앱 구조

**파일**: `apps/api/src/api/webapp.py`

- FastAPI 앱 생성
- lifespan에서 DB 풀 초기화/정리
- CORS 허용
- 아래 라우터 등록

## 등록 라우터

| 라우터 | Prefix | 설명 |
|--------|--------|------|
| `document` | `/documents` | 업로드, 파싱, 검색, 재인덱싱, 삭제 |
| `auth` | `/auth` | 회원가입, 로그인, 토큰 갱신 |
| `settings` | `/settings` | 임베드 설정 조회/수정 |
| `faq` | `/faqs` | FAQ CRUD |
| `conversation` | `/conversations` | 대화 조회/삭제 |
| `api_config` | `/api-configs` | 외부 API 설정 |
| `lead` | `/leads` | 리드 수집 설정/조회 |

## 인증

- `get_current_company()`가 대부분의 라우터에서 사용되는 핵심 의존성
- Bearer JWT를 해석해 회사 정보를 반환
- 실패 시 `HTTPException(401)`

## 문서 처리 흐름

`POST /documents/parse`

```text
업로드 → 해시 계산 → parsed_files 캐시 확인
      → Upstage 파싱 또는 S3 캐시 로드
      → 청킹 / 테이블 요약 / 임베딩
      → Qdrant 인덱싱
      → documents 메타데이터 저장
```
