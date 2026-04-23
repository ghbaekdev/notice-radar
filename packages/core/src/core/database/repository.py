import json
import logging
from uuid import UUID

from .connection import get_db_pool

logger = logging.getLogger(__name__)


class ParsedFileRepository:
    """Repository for parsed_files table operations (global parse cache)"""

    async def get_by_hash(self, file_hash: str) -> dict | None:
        """Get parsed file record by file hash (SHA256)"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, file_hash, s3_key, original_filename, file_type, file_size, created_at
                FROM parsed_files
                WHERE file_hash = $1
                """,
                file_hash
            )
            if row:
                logger.info(f"[ParsedFile] Cache hit for hash: {file_hash[:16]}...")
                return dict(row)
            return None

    async def create(self, data: dict) -> dict:
        """Create new parsed file record"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO parsed_files (file_hash, s3_key, original_filename, file_type, file_size)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, file_hash, s3_key, original_filename, file_type, file_size, created_at
                """,
                data["file_hash"],
                data["s3_key"],
                data["original_filename"],
                data["file_type"],
                data["file_size"],
            )

            logger.info(f"[ParsedFile] Created: hash={data['file_hash'][:16]}..., s3_key={data['s3_key']}")
            return dict(row)

    async def get_all(self) -> list[dict]:
        """Get all parsed files"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, file_hash, s3_key, original_filename, file_type, file_size, created_at
                FROM parsed_files
                ORDER BY created_at DESC
                """
            )
            return [dict(row) for row in rows]

    async def get_by_id(self, parsed_file_id: UUID) -> dict | None:
        """Get parsed file by ID"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, file_hash, s3_key, original_filename, file_type, file_size, created_at
                FROM parsed_files
                WHERE id = $1
                """,
                parsed_file_id
            )
            return dict(row) if row else None


class CompanyRepository:
    """Repository for companies table operations"""

    async def get_or_create(self, name: str) -> dict:
        """Get existing company or create new one"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            # Try to get existing company
            row = await conn.fetchrow(
                "SELECT id, name, display_name, created_at, updated_at FROM companies WHERE name = $1",
                name
            )

            if row:
                logger.info(f"[Company] Found existing: {name}")
                return dict(row)

            # Create new company
            row = await conn.fetchrow(
                """
                INSERT INTO companies (name, display_name)
                VALUES ($1, $2)
                RETURNING id, name, display_name, created_at, updated_at
                """,
                name,
                name  # display_name defaults to name
            )

            logger.info(f"[Company] Created new: {name}")
            return dict(row)

    async def get_all(self) -> list[dict]:
        """Get all companies"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, display_name, created_at, updated_at
                FROM companies
                ORDER BY created_at DESC
                """
            )
            return [dict(row) for row in rows]

    async def get_by_name(self, name: str) -> dict | None:
        """Get company by name"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, display_name, password_hash, created_at, updated_at FROM companies WHERE name = $1",
                name
            )
            return dict(row) if row else None

    async def get_by_id(self, company_id: UUID) -> dict | None:
        """Get company by ID"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, display_name, created_at, updated_at FROM companies WHERE id = $1",
                company_id
            )
            return dict(row) if row else None

    async def create_with_password(self, name: str, password_hash: str) -> dict:
        """Create a new company with password hash"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO companies (name, display_name, password_hash)
                VALUES ($1, $2, $3)
                RETURNING id, name, display_name, password_hash, created_at, updated_at
                """,
                name,
                name,  # display_name defaults to name
                password_hash,
            )

            logger.info(f"[Company] Created with password: {name}")
            return dict(row)

    async def update_password(self, company_id: UUID, password_hash: str) -> bool:
        """Update company password hash"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE companies
                SET password_hash = $2, updated_at = NOW()
                WHERE id = $1
                """,
                company_id,
                password_hash,
            )

            updated = result == "UPDATE 1"
            if updated:
                logger.info(f"[Company] Password updated: {company_id}")
            return updated


class DocumentRepository:
    """Repository for documents table operations"""

    async def create(self, doc_data: dict) -> dict:
        """Create new document record (with optional parsed_file_id reference)"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            # Check if parsed_file_id is provided
            parsed_file_id = doc_data.get("parsed_file_id")

            if parsed_file_id:
                row = await conn.fetchrow(
                    """
                    INSERT INTO documents (id, company_id, parsed_file_id, original_filename, file_type, file_size, chunk_count)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id, company_id, parsed_file_id, original_filename, file_type, file_size, chunk_count,
                              parsed_at, status, created_at, updated_at
                    """,
                    doc_data["id"],
                    doc_data["company_id"],
                    parsed_file_id,
                    doc_data["original_filename"],
                    doc_data["file_type"],
                    doc_data["file_size"],
                    doc_data["chunk_count"],
                )
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO documents (id, company_id, original_filename, file_type, file_size, chunk_count)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id, company_id, parsed_file_id, original_filename, file_type, file_size, chunk_count,
                              parsed_at, status, created_at, updated_at
                    """,
                    doc_data["id"],
                    doc_data["company_id"],
                    doc_data["original_filename"],
                    doc_data["file_type"],
                    doc_data["file_size"],
                    doc_data["chunk_count"],
                )

            logger.info(f"[Document] Created: {doc_data['id']}" + (f" (parsed_file_id={parsed_file_id})" if parsed_file_id else ""))
            return dict(row)

    async def get_by_company(self, company_id: UUID) -> list[dict]:
        """Get all documents for a company"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, company_id, parsed_file_id, original_filename, file_type, file_size, chunk_count,
                       parsed_at, status, created_at, updated_at
                FROM documents
                WHERE company_id = $1 AND status = 'active'
                ORDER BY created_at DESC
                """,
                company_id
            )
            return [dict(row) for row in rows]

    async def get_by_id(self, doc_id: UUID) -> dict | None:
        """Get document by ID"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, company_id, parsed_file_id, original_filename, file_type, file_size, chunk_count,
                       parsed_at, status, created_at, updated_at
                FROM documents
                WHERE id = $1
                """,
                doc_id
            )
            return dict(row) if row else None

    async def delete(self, doc_id: UUID) -> bool:
        """Hard delete document from database"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM documents WHERE id = $1
                """,
                doc_id
            )

            deleted = result == "DELETE 1"
            if deleted:
                logger.info(f"[Document] Deleted: {doc_id}")
            return deleted

    async def get_all_ids_by_company(self, company_id: UUID) -> list[UUID]:
        """Get all document IDs for a company (active only)"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id FROM documents
                WHERE company_id = $1 AND status = 'active'
                """,
                company_id
            )
            return [row["id"] for row in rows]

    async def delete_all_by_company(self, company_id: UUID) -> int:
        """Hard delete all documents for a company, return deleted count"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM documents WHERE company_id = $1
                """,
                company_id
            )

            # Parse "DELETE N" to get count
            deleted_count = int(result.split()[-1]) if result else 0
            logger.info(f"[Document] Deleted {deleted_count} documents for company {company_id}")
            return deleted_count

    async def update_chunk_count(self, doc_id: UUID, chunk_count: int) -> bool:
        """Update chunk_count for a document (used after reindexing)"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE documents
                SET chunk_count = $2, updated_at = NOW()
                WHERE id = $1 AND status = 'active'
                """,
                doc_id,
                chunk_count
            )

            updated = result == "UPDATE 1"
            if updated:
                logger.info(f"[Document] Updated chunk_count: {doc_id} -> {chunk_count}")
            return updated

    async def upsert(self, doc_data: dict) -> dict:
        """Create or update document record (INSERT ... ON CONFLICT DO UPDATE)"""
        pool = get_db_pool()

        async with pool.acquire() as conn:
            parsed_file_id = doc_data.get("parsed_file_id")

            if parsed_file_id:
                row = await conn.fetchrow(
                    """
                    INSERT INTO documents (id, company_id, parsed_file_id, original_filename, file_type, file_size, chunk_count)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (id) DO UPDATE SET
                        company_id = EXCLUDED.company_id,
                        parsed_file_id = EXCLUDED.parsed_file_id,
                        original_filename = EXCLUDED.original_filename,
                        file_type = EXCLUDED.file_type,
                        file_size = EXCLUDED.file_size,
                        chunk_count = EXCLUDED.chunk_count,
                        updated_at = NOW()
                    RETURNING id, company_id, parsed_file_id, original_filename, file_type, file_size, chunk_count,
                              parsed_at, status, created_at, updated_at
                    """,
                    doc_data["id"],
                    doc_data["company_id"],
                    parsed_file_id,
                    doc_data["original_filename"],
                    doc_data["file_type"],
                    doc_data["file_size"],
                    doc_data["chunk_count"],
                )
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO documents (id, company_id, original_filename, file_type, file_size, chunk_count)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (id) DO UPDATE SET
                        company_id = EXCLUDED.company_id,
                        original_filename = EXCLUDED.original_filename,
                        file_type = EXCLUDED.file_type,
                        file_size = EXCLUDED.file_size,
                        chunk_count = EXCLUDED.chunk_count,
                        updated_at = NOW()
                    RETURNING id, company_id, parsed_file_id, original_filename, file_type, file_size, chunk_count,
                              parsed_at, status, created_at, updated_at
                    """,
                    doc_data["id"],
                    doc_data["company_id"],
                    doc_data["original_filename"],
                    doc_data["file_type"],
                    doc_data["file_size"],
                    doc_data["chunk_count"],
                )

            logger.info(f"[Document] Upserted: {doc_data['id']}")
            return dict(row)


class FAQRepository:
    """FAQ 관리 리포지토리"""

    async def create(self, company_id: UUID, question: str, answer: str) -> dict:
        """FAQ 생성"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO faqs (company_id, question, answer)
                VALUES ($1, $2, $3)
                RETURNING *
                """,
                company_id, question, answer
            )
            logger.info(f"[FAQRepository] Created FAQ: {row['id']}")
            return dict(row)

    async def get_by_company(self, company_id: UUID, is_active: bool | None = None) -> list[dict]:
        """회사별 FAQ 목록 조회"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            if is_active is not None:
                rows = await conn.fetch(
                    """
                    SELECT * FROM faqs
                    WHERE company_id = $1 AND is_active = $2
                    ORDER BY created_at DESC
                    """,
                    company_id, is_active
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM faqs
                    WHERE company_id = $1
                    ORDER BY created_at DESC
                    """,
                    company_id
                )
            logger.info(f"[FAQRepository] Retrieved {len(rows)} FAQs for company {company_id}")
            return [dict(row) for row in rows]

    async def get_by_id(self, faq_id: UUID) -> dict | None:
        """FAQ ID로 조회"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM faqs WHERE id = $1",
                faq_id
            )
            return dict(row) if row else None

    async def update(self, faq_id: UUID, **kwargs) -> dict | None:
        """FAQ 수정"""
        allowed_fields = {'question', 'answer', 'is_active'}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields and v is not None}

        if not updates:
            return await self.get_by_id(faq_id)

        set_clause = ", ".join([f"{k} = ${i+2}" for i, k in enumerate(updates.keys())])
        set_clause += ", updated_at = NOW()"

        pool = get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE faqs SET {set_clause} WHERE id = $1 RETURNING *",
                faq_id, *updates.values()
            )
            if row:
                logger.info(f"[FAQRepository] Updated FAQ: {faq_id}")
            return dict(row) if row else None

    async def delete(self, faq_id: UUID) -> bool:
        """FAQ 삭제"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM faqs WHERE id = $1",
                faq_id
            )
            deleted = result == "DELETE 1"
            if deleted:
                logger.info(f"[FAQRepository] Deleted FAQ: {faq_id}")
            return deleted


class ConversationRepository:
    """대화 로그 리포지토리"""

    async def create_or_get(self, company_id: UUID, thread_id: str, source: str = "embed") -> dict:
        """대화 생성 또는 기존 반환 (thread_id UNIQUE)"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM conversations WHERE thread_id = $1",
                thread_id
            )
            if row:
                return dict(row)

            row = await conn.fetchrow(
                """
                INSERT INTO conversations (company_id, thread_id, source)
                VALUES ($1, $2, $3)
                RETURNING *
                """,
                company_id, thread_id, source
            )
            logger.info(f"[ConversationRepository] Created conversation: {row['id']}")
            return dict(row)

    async def get_by_company(self, company_id: UUID, limit: int = 20, offset: int = 0, search: str | None = None) -> list[dict]:
        """회사별 대화 목록 조회 (페이지네이션 + 검색)"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            if search:
                rows = await conn.fetch(
                    """
                    SELECT * FROM conversations
                    WHERE company_id = $1 AND first_message ILIKE $4 AND message_count > 0
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    company_id, limit, offset, f"%{search}%"
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM conversations
                    WHERE company_id = $1 AND message_count > 0
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    company_id, limit, offset
                )
            return [dict(row) for row in rows]

    async def count_by_company(self, company_id: UUID, search: str | None = None) -> int:
        """회사별 대화 총 개수"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            if search:
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(*) as count FROM conversations
                    WHERE company_id = $1 AND first_message ILIKE $2 AND message_count > 0
                    """,
                    company_id, f"%{search}%"
                )
            else:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) as count FROM conversations WHERE company_id = $1 AND message_count > 0",
                    company_id
                )
            return row["count"]

    async def get_by_id(self, conversation_id: UUID) -> dict | None:
        """단일 대화 조회"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM conversations WHERE id = $1",
                conversation_id
            )
            return dict(row) if row else None

    async def add_message(self, conversation_id: UUID, role: str, content: str, sources: list | None = None, execution_trace: list | None = None) -> dict:
        """메시지 추가 + message_count 업데이트 + first_message 설정"""
        import json
        pool = get_db_pool()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO conversation_messages (conversation_id, role, content, sources, execution_trace)
                VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)
                RETURNING *
                """,
                conversation_id, role, content,
                json.dumps(sources) if sources else "[]",
                json.dumps(execution_trace) if execution_trace else None
            )

            await conn.execute(
                """
                UPDATE conversations
                SET message_count = message_count + 1, updated_at = NOW()
                WHERE id = $1
                """,
                conversation_id
            )

            if role == "human":
                await conn.execute(
                    """
                    UPDATE conversations
                    SET first_message = $2
                    WHERE id = $1 AND first_message IS NULL
                    """,
                    conversation_id, content[:200]
                )

            logger.info(f"[ConversationRepository] Added {role} message to {conversation_id}")
            return dict(row)

    async def get_messages(self, conversation_id: UUID) -> list[dict]:
        """대화의 메시지 목록"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM conversation_messages
                WHERE conversation_id = $1
                ORDER BY created_at ASC
                """,
                conversation_id
            )
            return [dict(row) for row in rows]

    async def delete(self, conversation_id: UUID) -> bool:
        """대화 삭제 (CASCADE로 메시지도 삭제)"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM conversations WHERE id = $1",
                conversation_id
            )
            deleted = result == "DELETE 1"
            if deleted:
                logger.info(f"[ConversationRepository] Deleted conversation: {conversation_id}")
            return deleted

class ApiConfigRepository:
    """Repository for api_configs table operations"""

    async def create(self, company_id, data: dict) -> dict:
        pool = get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO api_configs (company_id, name, endpoint, method, headers, auth_type, auth_config, timeout_seconds, request_template, response_mapping)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7::jsonb, $8, $9::jsonb, $10::jsonb)
                RETURNING *
                """,
                company_id,
                data["name"],
                data["endpoint"],
                data.get("method", "POST"),
                json.dumps(data.get("headers", {})),
                data.get("auth_type", "none"),
                json.dumps(data.get("auth_config", {})),
                data.get("timeout_seconds", 30.0),
                json.dumps(data.get("request_template", {})),
                json.dumps(data.get("response_mapping", {})),
            )
            logger.info(f"[ApiConfigRepository] Created: {row['id']}")
            return dict(row)

    async def get_by_company(self, company_id) -> list[dict]:
        pool = get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM api_configs WHERE company_id = $1 ORDER BY name",
                company_id
            )
            return [dict(r) for r in rows]

    async def get_by_id(self, config_id) -> dict | None:
        pool = get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM api_configs WHERE id = $1", config_id)
            return dict(row) if row else None

    async def get_by_company_and_name(self, company_id, name: str) -> dict | None:
        pool = get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM api_configs WHERE company_id = $1 AND name = $2",
                company_id, name
            )
            return dict(row) if row else None

    async def update(self, config_id, **kwargs) -> dict | None:
        pool = get_db_pool()
        if not kwargs:
            return await self.get_by_id(config_id)

        set_parts = []
        values = [config_id]
        idx = 2

        json_fields = {"headers", "auth_config", "request_template", "response_mapping"}

        for key, value in kwargs.items():
            if key in json_fields:
                set_parts.append(f"{key} = ${idx}::jsonb")
                values.append(json.dumps(value))
            else:
                set_parts.append(f"{key} = ${idx}")
                values.append(value)
            idx += 1

        set_parts.append("updated_at = NOW()")
        set_clause = ", ".join(set_parts)

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"UPDATE api_configs SET {set_clause} WHERE id = $1 RETURNING *",
                *values
            )
            if row:
                logger.info(f"[ApiConfigRepository] Updated: {config_id}")
            return dict(row) if row else None

    async def delete(self, config_id) -> bool:
        pool = get_db_pool()
        async with pool.acquire() as conn:
            result = await conn.execute("DELETE FROM api_configs WHERE id = $1", config_id)
            if result == "DELETE 1":
                logger.info(f"[ApiConfigRepository] Deleted: {config_id}")
                return True
            return False


class LeadRepository:
    """리드(잠재고객) 리포지토리"""

    async def create(self, data: dict) -> dict:
        """리드 저장"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO leads (lead_name, representative_name, representative_phone, region_name, source_name, hq_response, slack_sent)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                RETURNING *
                """,
                data["lead_name"],
                data["representative_name"],
                data["representative_phone"],
                data.get("region_name"),
                data.get("source_name", "CHATBOT"),
                json.dumps(data.get("hq_response", {})),
                data.get("slack_sent", False),
            )
            logger.info(f"[LeadRepository] Created lead: {row['id']}")
            return dict(row)

    async def get_all(self, limit: int = 20, offset: int = 0, search: str | None = None) -> list[dict]:
        """리드 목록"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            if search:
                rows = await conn.fetch(
                    """
                    SELECT * FROM leads
                    WHERE lead_name ILIKE $1
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    f"%{search}%", limit, offset
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM leads
                    ORDER BY created_at DESC
                    LIMIT $1 OFFSET $2
                    """,
                    limit, offset
                )
            return [dict(row) for row in rows]

    async def count_all(self, search: str | None = None) -> int:
        """리드 총 개수"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            if search:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) as cnt FROM leads WHERE lead_name ILIKE $1",
                    f"%{search}%"
                )
            else:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) as cnt FROM leads"
                )
            return row["cnt"]

    async def get_by_id(self, lead_id: UUID) -> dict | None:
        """리드 ID로 조회"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM leads WHERE id = $1",
                lead_id
            )
            return dict(row) if row else None

    async def delete(self, lead_id: UUID) -> bool:
        """리드 삭제"""
        pool = get_db_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM leads WHERE id = $1",
                lead_id
            )
            deleted = result == "DELETE 1"
            if deleted:
                logger.info(f"[LeadRepository] Deleted lead: {lead_id}")
            return deleted
