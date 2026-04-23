"""FastAPI web application for document processing API.

This module provides HTTP endpoints for document parsing, chunking, and search.
It's served alongside the LangGraph agent via the http configuration in langgraph.json.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.database import close_db, get_db_pool, init_db

# Silence noisy third-party loggers so pipeline tracing logs are visible
for _name in (
    "langgraph_runtime_inmem",
    "langgraph_api",
    "httpcore",
    "httpx",
    "urllib3",
    "watchfiles",
    "langsmith",
    "openai",
):
    logging.getLogger(_name).setLevel(logging.WARNING)
from .routers import (
    api_config_router,
    auth_router,
    conversation_router,
    document_router,
    faq_router,
    lead_router,
    settings_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize and cleanup resources"""
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="RAG Agent Document API",
    description="Document parsing, chunking, and hybrid search API for RAG agent",
    version="1.0.0",
    lifespan=lifespan,
    root_path=os.getenv("ROOT_PATH", ""),
)

# CORS middleware for Next.js frontend
_default_origins = "http://localhost:3000,https://smith.langchain.com"
_cors_origins = os.getenv("CORS_ORIGINS", _default_origins).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    try:
        pool = get_db_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ok"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})


app.include_router(document_router)
app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(faq_router)
app.include_router(conversation_router)
app.include_router(api_config_router)
app.include_router(lead_router)
