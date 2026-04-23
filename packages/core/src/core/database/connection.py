import logging
import os
from urllib.parse import quote_plus

import asyncpg

logger = logging.getLogger(__name__)

# Global connection pool
_db_pool: asyncpg.Pool | None = None


def get_database_url() -> str:
    """Get database URL from environment variables"""
    # Support both POSTGRES_HOST and POSTGRES_URI (strip http:// prefix if present)
    host = os.getenv("POSTGRES_HOST")
    if not host:
        uri = os.getenv("POSTGRES_URI", "localhost")
        host = uri.replace("http://", "").replace("https://", "").rstrip("/")

    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    password_encoded = quote_plus(password)  # URL encode special characters
    # Support both POSTGRES_DB and POSTGRES_DATABASE
    database = os.getenv("POSTGRES_DB") or os.getenv("POSTGRES_DATABASE", "documents")

    return f"postgresql://{user}:{password_encoded}@{host}:{port}/{database}"


async def create_pool() -> asyncpg.Pool:
    """Create asyncpg connection pool"""
    database_url = get_database_url()
    logger.info("[Database] Connecting to PostgreSQL...")

    pool = await asyncpg.create_pool(
        database_url,
        min_size=2,
        max_size=10,
        command_timeout=60,
    )

    logger.info("[Database] Connection pool created successfully")
    return pool


async def init_db() -> None:
    """Initialize database: create connection pool"""
    global _db_pool
    _db_pool = await create_pool()
    logger.info("[Database] Database initialized successfully")


async def close_db() -> None:
    """Close database connection pool"""
    global _db_pool

    if _db_pool:
        await _db_pool.close()
        _db_pool = None
        logger.info("[Database] Connection pool closed")


def get_db_pool() -> asyncpg.Pool:
    """Get the global database connection pool"""
    if _db_pool is None:
        raise RuntimeError("Database pool not initialized. Call init_db() first.")
    return _db_pool
