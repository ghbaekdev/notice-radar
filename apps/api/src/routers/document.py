"""Document parsing, chunking, and hybrid search API endpoints."""

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import boto3
import httpx
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from google import genai
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    Modifier,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from core.database import CompanyRepository, DocumentRepository, ParsedFileRepository
from core.shared.vector_search import (
    QDRANT_HOST,
    QDRANT_PORT,
    get_collection_name,
    get_sparse_embeddings,
)
from dependencies import get_current_company

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

UPSTAGE_API_URL = "https://api.upstage.ai/v1/document-digitization"
EMBEDDING_DIMENSION = 3072  # gemini-embedding-001 dimension

# Chunking settings
MAX_CHUNK_SIZE = 700
CHUNK_OVERLAP = 150

# Table context settings
TABLE_CONTEXT_KEYWORDS = [
    "표",
    "table",
    "아래",
    "다음",
    "following",
    "below",
    "shows",
    "보여",
    "정리",
    "요약",
    "데이터",
    "결과",
]
MAX_CONTEXT_LENGTH = 200

# Cache directory (local fallback)
CACHE_DIR = Path(__file__).parent.parent.parent.parent / "cache" / "documents"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# S3 settings
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "wiseai-agent-builder")
S3_PARSED_PREFIX = "parsed-documents"


# =============================================================================
# S3 Cache Functions
# =============================================================================


def calculate_file_hash(file_bytes: bytes) -> str:
    """Calculate SHA256 hash"""
    return hashlib.sha256(file_bytes).hexdigest()


def get_s3_client():
    """Return boto3 S3 client"""
    return boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION", "ap-northeast-2"),
    )


def get_s3_key(file_hash: str) -> str:
    """Generate S3 key: parsed-documents/{file_hash}.json"""
    return f"{S3_PARSED_PREFIX}/{file_hash}.json"


def save_to_s3(file_hash: str, data: dict) -> str:
    """Save parse result to S3, return S3 key"""
    s3 = get_s3_client()
    s3_key = get_s3_key(file_hash)

    try:
        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key,
            Body=json.dumps(data, ensure_ascii=False, indent=2),
            ContentType="application/json",
        )
        logger.info(f"[S3 saved] s3://{S3_BUCKET_NAME}/{s3_key}")
        return s3_key
    except ClientError as e:
        logger.error(f"[S3 save failed] {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save to S3: {e}")


def load_from_s3(s3_key: str) -> dict | None:
    """Load parse result from S3"""
    s3 = get_s3_client()

    try:
        response = s3.get_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
        body = response["Body"].read().decode("utf-8")
        data = json.loads(body)
        logger.info(f"[S3 loaded] s3://{S3_BUCKET_NAME}/{s3_key}")
        return data
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            logger.warning(f"[S3] Key not found: {s3_key}")
            return None
        logger.error(f"[S3 load failed] {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load from S3: {e}")


def save_parsed_cache(cache_data: dict[str, Any]) -> Path:
    """Save parsed result to local file cache."""
    cache_path = CACHE_DIR / f"{cache_data['document_id']}.json"
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)

    logger.info(f"[Cache saved] {cache_path}")
    return cache_path


def load_parsed_cache(document_id: str) -> dict | None:
    """Load cached parse result"""
    cache_path = CACHE_DIR / f"{document_id}.json"
    if cache_path.exists():
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    return None


# =============================================================================
# Pydantic Models
# =============================================================================


class Chunk(BaseModel):
    id: str
    heading: str
    content: str
    level: int
    order: int
    parent_heading: str | None = None
    parent_chunk_id: str | None = None
    parent_level: int | None = None
    sibling_chunk_ids: list[str] = []
    hierarchy_path: str | None = None
    content_type: str = "text"
    contextual_description: str | None = None
    has_context: bool = False
    table_context: str | None = None


class IndexedDocument(BaseModel):
    document_id: str
    original_filename: str
    file_type: str
    file_size: int
    chunk_count: int
    chunks: list[Chunk]


# =============================================================================
# Content Processing Functions
# =============================================================================

TEXT_FILE_TYPES = ("md", "txt", "csv")
PDF_MIN_TEXT_LENGTH_FOR_FALLBACK = 20
EMBEDDING_BATCH_SIZE = 10
EMBEDDING_BATCH_SLEEP_SECONDS = 0.5
EMBEDDING_RETRY_SLEEP_SECONDS = 2.0
EMBEDDING_MAX_RETRIES = 3
PDF_TABLE_KEYWORDS = (
    "표",
    "구분",
    "항목",
    "합계",
    "금액",
    "면적",
    "임대",
    "보증금",
    "주택",
    "제출서류",
    "소득",
    "자산",
    "일정",
)


def get_file_type(filename: str) -> str | None:
    extension = filename.split(".")[-1].lower()
    if extension in ["pdf", "png", "jpg", "jpeg", "tiff", "bmp", "md", "txt", "csv"]:
        return extension
    return None


def is_text_file(file_type: str) -> bool:
    return file_type in TEXT_FILE_TYPES


def csv_to_markdown(file_bytes: bytes) -> str:
    """Convert CSV to a single markdown table."""
    text = file_bytes.decode("utf-8")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return ""

    header = rows[0]
    md_lines = []
    md_lines.append("| " + " | ".join(header) + " |")
    md_lines.append("| " + " | ".join(["---"] * len(header)) + " |")
    for row in rows[1:]:
        padded = row + [""] * (len(header) - len(row))
        md_lines.append("| " + " | ".join(padded[: len(header)]) + " |")

    return "\n".join(md_lines)


def detect_content_type(content: str) -> str:
    """Detect content type: table / list / text"""
    lines = content.strip().split("\n")
    if not lines:
        return "text"

    table_lines = [
        line
        for line in lines
        if line.strip().startswith("|") and line.strip().endswith("|")
    ]
    if len(table_lines) >= 2:
        return "table"

    list_patterns = [r"^\s*[-*+]\s", r"^\s*\d+\.\s"]
    list_lines = sum(
        1 for line in lines if any(re.match(p, line) for p in list_patterns)
    )
    if lines and list_lines / len(lines) >= 0.5:
        return "list"

    return "text"


def build_parse_cache_data(
    document_id: str,
    filename: str,
    file_type: str,
    file_size: int,
    markdown: str,
    *,
    file_hash: str | None = None,
    parse_strategy: str | None = None,
    general_parse: dict[str, Any] | None = None,
    hybrid_parse: dict[str, Any] | None = None,
    upstage_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build cache payload for local/S3 storage."""
    cache_data: dict[str, Any] = {
        "document_id": document_id,
        "original_filename": filename,
        "file_type": file_type,
        "file_size": file_size,
        "parsed_at": datetime.now().isoformat(),
        "markdown": markdown,
        "upstage_response": upstage_response,
    }

    if file_hash:
        cache_data["file_hash"] = file_hash
    if parse_strategy:
        cache_data["parse_strategy"] = parse_strategy
    if general_parse is not None:
        cache_data["general_parse"] = general_parse
    if hybrid_parse is not None:
        cache_data["hybrid_parse"] = hybrid_parse

    return cache_data


def is_current_pdf_cache(cache_data: dict[str, Any] | None) -> bool:
    """Return True when cached PDF parse matches the current local strategy."""
    if not cache_data:
        return False

    return cache_data.get("parse_strategy") == "pdf_table_aware"


def normalize_pdf_text(text: str) -> str:
    """Normalize extracted PDF text while preserving line breaks."""
    cleaned = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "\n".join(line.rstrip() for line in cleaned.split("\n"))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def normalize_table_cell(cell: Any) -> str:
    """Normalize a single table cell to a compact string."""
    if cell is None:
        return ""

    text = str(cell).replace("\r\n", "\n").replace("\r", "\n")
    text = " ".join(part.strip() for part in text.split("\n") if part.strip())
    return re.sub(r"\s{2,}", " ", text).strip()


def table_rows_to_markdown(rows: list[list[Any] | tuple[Any, ...]]) -> str:
    """Convert extracted table rows to markdown."""
    normalized_rows = []
    for row in rows:
        normalized_row = [normalize_table_cell(cell) for cell in row]
        if any(cell for cell in normalized_row):
            normalized_rows.append(normalized_row)

    if not normalized_rows:
        return ""

    column_count = max(len(row) for row in normalized_rows)
    padded_rows = [row + [""] * (column_count - len(row)) for row in normalized_rows]

    if len(padded_rows) == 1:
        header = [f"col_{index + 1}" for index in range(column_count)]
        body_rows = padded_rows
    else:
        header = padded_rows[0]
        if not any(header):
            header = [f"col_{index + 1}" for index in range(column_count)]
        body_rows = padded_rows[1:]

    markdown_lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * column_count) + " |",
    ]
    markdown_lines.extend("| " + " | ".join(row) + " |" for row in body_rows)
    return "\n".join(markdown_lines)


def merge_page_text_and_tables(text: str, table_markdowns: list[str]) -> str:
    """Merge non-table text and table markdown blocks for a page."""
    sections = []
    normalized_text = normalize_pdf_text(text)
    if normalized_text:
        sections.append(normalized_text)

    sections.extend(table for table in table_markdowns if table.strip())
    return "\n\n".join(sections).strip()


def count_non_empty_lines(text: str) -> int:
    """Count non-empty lines in a block of text."""
    return sum(1 for line in text.splitlines() if line.strip())


def choose_general_pdf_page_markdown(default_text: str, layout_text: str) -> str:
    """Prefer layout extraction when it preserves more useful structure."""
    default_text = normalize_pdf_text(default_text)
    layout_text = normalize_pdf_text(layout_text)

    if not layout_text:
        return default_text
    if not default_text:
        return layout_text

    default_lines = count_non_empty_lines(default_text)
    layout_lines = count_non_empty_lines(layout_text)

    if layout_lines >= default_lines + 2 and len(layout_text) >= int(
        len(default_text) * 0.8
    ):
        return layout_text

    return default_text


def build_pdf_page_record(
    page_number: int,
    markdown: str,
    *,
    source: str = "general",
) -> dict[str, Any]:
    """Create a normalized per-page parse record."""
    normalized = normalize_pdf_text(markdown)
    lines = [line for line in normalized.splitlines() if line.strip()]
    line_lengths = [len(line.strip()) for line in lines]

    return {
        "page": page_number,
        "markdown": normalized,
        "source": source,
        "text_length": len(re.sub(r"\s+", "", normalized)),
        "line_count": len(lines),
        "max_line_length": max(line_lengths, default=0),
        "avg_line_length": int(sum(line_lengths) / len(line_lengths))
        if line_lengths
        else 0,
    }


def is_unstructured_table_page(markdown: str, metrics: dict[str, Any]) -> bool:
    """Conservative heuristic for pages that likely contain broken table content."""
    numeric_tokens = re.findall(r"\d[\d,./()%:-]*", markdown)
    digit_count = sum(char.isdigit() for char in markdown)
    has_merged_numeric_blocks = bool(
        re.search(r"\d{1,3}(?:,\d{3}){1,}\d{1,3}(?:,\d{3}){1,}", markdown)
    )
    keyword_hits = sum(1 for keyword in PDF_TABLE_KEYWORDS if keyword in markdown)

    return (
        keyword_hits >= 1
        and (len(numeric_tokens) >= 6 or digit_count >= 20)
        and (
            has_merged_numeric_blocks
            or metrics.get("line_count", 0) <= 4
            or metrics.get("max_line_length", 0) >= 140
            or metrics.get("avg_line_length", 0) >= 80
        )
    )


def select_pdf_fallback_pages(general_pages: list[dict[str, Any]]) -> list[int]:
    """Select only obviously low-quality PDF pages for Upstage fallback."""
    fallback_pages: list[int] = []

    for page in general_pages:
        markdown = normalize_pdf_text(page.get("markdown", ""))
        metrics = {
            "text_length": page.get("text_length", len(re.sub(r"\s+", "", markdown))),
            "line_count": page.get("line_count", count_non_empty_lines(markdown)),
            "max_line_length": page.get(
                "max_line_length",
                max(
                    (
                        len(line.strip())
                        for line in markdown.splitlines()
                        if line.strip()
                    ),
                    default=0,
                ),
            ),
            "avg_line_length": page.get(
                "avg_line_length",
                int(
                    sum(
                        len(line.strip())
                        for line in markdown.splitlines()
                        if line.strip()
                    )
                    / max(count_non_empty_lines(markdown), 1)
                )
                if markdown
                else 0,
            ),
        }

        if metrics["text_length"] == 0:
            fallback_pages.append(page["page"])
            continue

        if metrics["text_length"] < PDF_MIN_TEXT_LENGTH_FOR_FALLBACK:
            fallback_pages.append(page["page"])
            continue

        if is_unstructured_table_page(markdown, metrics):
            fallback_pages.append(page["page"])

    return fallback_pages


def merge_pdf_pages(
    general_pages: list[dict[str, Any]],
    upstage_pages: dict[int, str],
) -> tuple[list[dict[str, Any]], str]:
    """Merge general parser pages with Upstage fallback pages."""
    merged_pages: list[dict[str, Any]] = []

    for page in general_pages:
        page_number = page["page"]
        if page_number in upstage_pages:
            merged_page = build_pdf_page_record(
                page_number,
                upstage_pages[page_number],
                source="upstage",
            )
        else:
            merged_page = {
                **page,
                "markdown": normalize_pdf_text(page.get("markdown", "")),
                "source": page.get("source", "general"),
            }
        merged_pages.append(merged_page)

    merged_markdown = "\n\n".join(
        page["markdown"] for page in merged_pages if page["markdown"].strip()
    ).strip()

    return merged_pages, merged_markdown


def get_pdf_reader_writer():
    """Import pypdf lazily so non-PDF paths do not require the dependency at import time."""
    try:
        from pypdf import PdfReader, PdfWriter
    except ModuleNotFoundError as e:
        raise RuntimeError("pypdf is required for hybrid PDF parsing") from e

    return PdfReader, PdfWriter


def get_pdfplumber_module():
    """Import pdfplumber lazily so non-PDF paths do not require the dependency at import time."""
    try:
        import pdfplumber
    except ModuleNotFoundError as e:
        raise RuntimeError("pdfplumber is required for table-aware PDF parsing") from e

    return pdfplumber


def object_overlaps_bbox(
    obj: dict[str, Any], bbox: tuple[float, float, float, float]
) -> bool:
    """Return True when a pdfplumber object overlaps a table bounding box."""
    x0 = obj.get("x0")
    x1 = obj.get("x1")
    top = obj.get("top")
    bottom = obj.get("bottom")
    if None in (x0, x1, top, bottom):
        return False

    bx0, btop, bx1, bbottom = bbox
    return not (x1 <= bx0 or x0 >= bx1 or bottom <= btop or top >= bbottom)


def extract_table_markdowns_from_pdfplumber_page(
    page: Any,
) -> tuple[list[str], list[tuple[float, float, float, float]]]:
    """Extract markdown tables and their bounding boxes from a pdfplumber page."""
    table_markdowns: list[str] = []
    table_bboxes: list[tuple[float, float, float, float]] = []

    for table in page.find_tables():
        rows = table.extract()
        markdown = table_rows_to_markdown(rows)
        if markdown:
            table_markdowns.append(markdown)
            table_bboxes.append(table.bbox)

    return table_markdowns, table_bboxes


def extract_non_table_text_from_pdfplumber_page(
    page: Any, table_bboxes: list[tuple[float, float, float, float]]
) -> str:
    """Extract text outside detected table regions from a pdfplumber page."""
    filtered_page = page
    if table_bboxes:
        filtered_page = page.filter(
            lambda obj: (
                not any(object_overlaps_bbox(obj, bbox) for bbox in table_bboxes)
            )
        )

    text = filtered_page.extract_text(layout=True) or filtered_page.extract_text() or ""
    return normalize_pdf_text(text)


def extract_pdf_pages_with_table_aware_parser(file_bytes: bytes) -> dict[str, Any]:
    """Extract PDF pages with pdfplumber and convert tables to markdown."""
    pdfplumber = get_pdfplumber_module()
    pages: list[dict[str, Any]] = []
    table_page_count = 0

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            table_markdowns, table_bboxes = (
                extract_table_markdowns_from_pdfplumber_page(page)
            )
            if table_markdowns:
                table_page_count += 1

            non_table_text = extract_non_table_text_from_pdfplumber_page(
                page, table_bboxes
            )
            markdown = merge_page_text_and_tables(non_table_text, table_markdowns)
            page_record = build_pdf_page_record(page_number, markdown)
            page_record["table_count"] = len(table_markdowns)
            pages.append(page_record)

    merged_markdown = "\n\n".join(
        page["markdown"] for page in pages if page["markdown"].strip()
    ).strip()

    return {
        "parser": "pdfplumber",
        "page_count": len(pages),
        "table_page_count": table_page_count,
        "pages": pages,
        "markdown": merged_markdown,
    }


def parse_pdf_with_table_aware_parser(
    *,
    document_id: str,
    filename: str,
    file_bytes: bytes,
    file_size: int,
    file_hash: str,
) -> dict[str, Any]:
    """Parse a PDF locally with table-aware extraction."""
    general_parse = extract_pdf_pages_with_table_aware_parser(file_bytes)
    markdown = general_parse["markdown"]

    cache_data = build_parse_cache_data(
        document_id=document_id,
        filename=filename,
        file_type="pdf",
        file_size=file_size,
        markdown=markdown,
        file_hash=file_hash,
        parse_strategy="pdf_table_aware",
        general_parse=general_parse,
    )

    return {
        "markdown": markdown,
        "cache_data": cache_data,
    }


def extract_pdf_pages_with_general_parser(file_bytes: bytes) -> dict[str, Any]:
    """Extract PDF pages with a local parser before selective Upstage fallback."""
    pdf_reader_cls, _ = get_pdf_reader_writer()
    reader = pdf_reader_cls(io.BytesIO(file_bytes))

    pages: list[dict[str, Any]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        default_text = normalize_pdf_text(page.extract_text() or "")
        try:
            layout_text = normalize_pdf_text(
                page.extract_text(extraction_mode="layout") or ""
            )
        except TypeError:
            layout_text = ""

        markdown = choose_general_pdf_page_markdown(default_text, layout_text)
        pages.append(build_pdf_page_record(page_number, markdown))

    merged_markdown = "\n\n".join(
        page["markdown"] for page in pages if page["markdown"].strip()
    ).strip()

    return {
        "parser": "pypdf",
        "page_count": len(pages),
        "pages": pages,
        "markdown": merged_markdown,
    }


def build_pdf_subset(file_bytes: bytes, pages: list[int]) -> bytes:
    """Create a PDF containing only the requested 1-indexed pages."""
    pdf_reader_cls, pdf_writer_cls = get_pdf_reader_writer()
    reader = pdf_reader_cls(io.BytesIO(file_bytes))
    writer = pdf_writer_cls()

    for page_number in pages:
        writer.add_page(reader.pages[page_number - 1])

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def extract_upstage_page_markdowns(result: dict[str, Any]) -> dict[int, str]:
    """Group Upstage markdown output by page number."""
    page_markdowns: dict[int, list[str]] = defaultdict(list)

    for element in result.get("elements", []):
        page_number = element.get("page")
        markdown = normalize_pdf_text(element.get("markdown", ""))
        if page_number and markdown:
            page_markdowns[int(page_number)].append(markdown)

    return {
        page_number: "\n\n".join(parts).strip()
        for page_number, parts in page_markdowns.items()
        if parts
    }


async def call_upstage_parse(
    *,
    api_key: str,
    filename: str,
    file_bytes: bytes,
    ocr: Literal["auto", "force", "skip"],
) -> dict[str, Any]:
    """Call Upstage document parse."""
    headers = {"Authorization": f"Bearer {api_key}"}
    files_data = {"document": (filename, file_bytes)}
    data = {
        "ocr": ocr,
        "model": "document-parse",
        "output_formats": '["markdown"]',
    }

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                UPSTAGE_API_URL,
                headers=headers,
                files=files_data,
                data=data,
            )

        if response.status_code != 200:
            logger.error(
                f"[Upstage error] status={response.status_code}, body={response.text}"
            )
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Upstage API error: {response.text}",
            )

        return response.json()
    except httpx.TimeoutException as e:
        raise HTTPException(
            status_code=504, detail="Upstage API request timed out"
        ) from e
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to connect to Upstage API: {e!s}",
        ) from e


async def parse_pdf_with_hybrid_fallback(
    *,
    document_id: str,
    filename: str,
    file_bytes: bytes,
    file_size: int,
    file_hash: str,
    api_key: str,
    ocr: Literal["auto", "force", "skip"],
) -> dict[str, Any]:
    """Parse a PDF locally first, then reparse only low-quality pages with Upstage."""
    general_parse = extract_pdf_pages_with_general_parser(file_bytes)
    general_pages = general_parse["pages"]
    fallback_pages = select_pdf_fallback_pages(general_pages)
    upstage_page_markdowns: dict[int, str] = {}
    upstage_response: dict[str, Any] | None = None

    if fallback_pages:
        subset_bytes = build_pdf_subset(file_bytes, fallback_pages)
        upstage_response = await call_upstage_parse(
            api_key=api_key,
            filename=filename,
            file_bytes=subset_bytes,
            ocr=ocr,
        )
        subset_page_markdowns = extract_upstage_page_markdowns(upstage_response)
        upstage_page_markdowns = {
            original_page: subset_page_markdowns.get(index, "")
            for index, original_page in enumerate(fallback_pages, start=1)
            if subset_page_markdowns.get(index, "")
        }

    merged_pages, markdown = merge_pdf_pages(general_pages, upstage_page_markdowns)

    cache_data = build_parse_cache_data(
        document_id=document_id,
        filename=filename,
        file_type="pdf",
        file_size=file_size,
        markdown=markdown,
        file_hash=file_hash,
        parse_strategy="hybrid_pdf",
        general_parse=general_parse,
        hybrid_parse={
            "fallback_pages": fallback_pages,
            "upstage_pages": upstage_page_markdowns,
            "pages": merged_pages,
        },
        upstage_response=upstage_response,
    )

    return {
        "markdown": markdown,
        "cache_data": cache_data,
    }


async def generate_contextual_description(
    chunk_content: str, full_markdown: str, hierarchy_path: str = ""
) -> str:
    """Generate contextual description using Gemini (Contextual Retrieval).

    Prepends a document-aware context to each chunk for better search retrieval.
    See: https://www.anthropic.com/news/contextual-retrieval
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not configured, skipping contextual description")
        return ""

    try:
        client = genai.Client(api_key=api_key)

        prompt = f"""<document>
{full_markdown}
</document>

다음은 위 문서에서 추출한 청크입니다:
<chunk>
{chunk_content}
</chunk>

{f"문서 내 위치: {hierarchy_path}" if hierarchy_path else ""}

이 청크를 문서 전체 맥락에서 위치시키는 짧은 설명을 작성해주세요.
검색 시 이 청크를 정확히 찾을 수 있도록, 핵심 엔티티(회사명, 제품명, 날짜 등)와 맥락을 포함해주세요.
설명만 작성하고 다른 내용은 포함하지 마세요."""

        response = await client.aio.models.generate_content(
            model="gemini-3-flash-preview", contents=prompt
        )
        description = response.text.strip()
        logger.info(f"[Contextual description] {len(description)} chars")
        return description
    except Exception as e:
        logger.warning(f"Contextual description generation failed: {e}")
        return ""


# =============================================================================
# Chunking Functions
# =============================================================================


def chunk_markdown_by_heading(markdown: str) -> list[dict]:
    """Chunk markdown by headings"""
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    chunks = []
    lines = markdown.split("\n")

    current_chunk = {"heading": "Introduction", "content": [], "level": 0, "order": 0}

    order = 0

    for line in lines:
        heading_match = heading_pattern.match(line)

        if heading_match:
            if current_chunk["content"]:
                content_text = "\n".join(current_chunk["content"]).strip()
                if content_text:
                    current_chunk["content"] = content_text
                    chunks.append(current_chunk)
                    order += 1

            level = len(heading_match.group(1))
            heading = heading_match.group(2).strip()
            current_chunk = {
                "heading": heading,
                "content": [],
                "level": level,
                "order": order,
            }
        else:
            current_chunk["content"].append(line)

    if current_chunk["content"]:
        content_text = "\n".join(current_chunk["content"]).strip()
        if content_text:
            current_chunk["content"] = content_text
            chunks.append(current_chunk)

    chunks = [c for c in chunks if c["content"]]

    logger.info(f"[Chunking] {len(chunks)} chunks created")
    return chunks


def is_table_line(line: str) -> bool:
    """Check if line is markdown table line"""
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|")


def extract_tables_and_text(content: str) -> list[dict]:
    """Extract tables and text separately (preserve tables intact)"""
    lines = content.split("\n")
    segments = []
    current_segment = {"type": "text", "lines": []}

    i = 0
    while i < len(lines):
        line = lines[i]

        if is_table_line(line):
            if current_segment["lines"]:
                segments.append(current_segment)

            table_lines = []
            while i < len(lines) and (
                is_table_line(lines[i]) or lines[i].strip() == ""
            ):
                if lines[i].strip():
                    table_lines.append(lines[i])
                i += 1

            if table_lines:
                segments.append({"type": "table", "lines": table_lines})

            current_segment = {"type": "text", "lines": []}
        else:
            current_segment["lines"].append(line)
            i += 1

    if current_segment["lines"]:
        segments.append(current_segment)

    return segments


def split_by_paragraphs(
    section: dict, max_size: int = MAX_CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[dict]:
    """Split long sections by paragraphs (with overlap, parent info, table preservation)"""
    content = section["content"]
    heading = section["heading"]
    level = section["level"]
    base_order = section["order"]

    segments = extract_tables_and_text(content)

    paragraphs = []
    for seg in segments:
        if seg["type"] == "table":
            table_text = "\n".join(seg["lines"])
            if table_text.strip():
                paragraphs.append(("[TABLE]", table_text))
        else:
            text = "\n".join(seg["lines"])
            for para in re.split(r"\n\s*\n", text):
                if para.strip():
                    paragraphs.append(("text", para.strip()))

    chunks = []
    current_chunk = ""
    sub_order = 0

    for _, (para_type, para_content) in enumerate(paragraphs):
        if not para_content.strip():
            continue

        if para_type == "[TABLE]":
            table_context = ""

            if (
                current_chunk.strip()
                and len(current_chunk.strip()) < MAX_CONTEXT_LENGTH
                and any(kw in current_chunk.lower() for kw in TABLE_CONTEXT_KEYWORDS)
            ):
                table_context = current_chunk.strip()
                combined_content = f"{table_context}\n\n{para_content}"

                chunks.append(
                    {
                        "heading": heading,
                        "content": combined_content,
                        "content_type": "table",
                        "has_context": True,
                        "table_context": table_context,
                        "level": level,
                        "order": base_order,
                        "sub_order": sub_order,
                        "parent_heading": section.get("parent_heading"),
                        "parent_chunk_id": section.get("parent_chunk_id"),
                        "parent_level": section.get("parent_level"),
                        "hierarchy_path": section.get("hierarchy_path"),
                    }
                )
                sub_order += 1
                current_chunk = ""
                continue

            if current_chunk.strip():
                chunks.append(
                    {
                        "heading": heading,
                        "content": current_chunk.strip(),
                        "content_type": detect_content_type(current_chunk.strip()),
                        "level": level,
                        "order": base_order,
                        "sub_order": sub_order,
                        "parent_heading": section.get("parent_heading"),
                        "parent_chunk_id": section.get("parent_chunk_id"),
                        "parent_level": section.get("parent_level"),
                        "hierarchy_path": section.get("hierarchy_path"),
                    }
                )
                sub_order += 1
                current_chunk = ""

            chunks.append(
                {
                    "heading": heading,
                    "content": para_content,
                    "content_type": "table",
                    "has_context": False,
                    "level": level,
                    "order": base_order,
                    "sub_order": sub_order,
                    "parent_heading": section.get("parent_heading"),
                    "parent_chunk_id": section.get("parent_chunk_id"),
                    "parent_level": section.get("parent_level"),
                    "hierarchy_path": section.get("hierarchy_path"),
                }
            )
            sub_order += 1
            continue

        if len(current_chunk) + len(para_content) > max_size and current_chunk:
            content_to_save = current_chunk.strip()
            chunks.append(
                {
                    "heading": heading,
                    "content": content_to_save,
                    "content_type": detect_content_type(content_to_save),
                    "level": level,
                    "order": base_order,
                    "sub_order": sub_order,
                    "parent_heading": section.get("parent_heading"),
                    "parent_chunk_id": section.get("parent_chunk_id"),
                    "parent_level": section.get("parent_level"),
                    "hierarchy_path": section.get("hierarchy_path"),
                }
            )
            sub_order += 1
            current_chunk = current_chunk[-overlap:] if overlap else ""

        current_chunk += para_content + "\n\n"

    if current_chunk.strip():
        content_to_save = current_chunk.strip()
        chunks.append(
            {
                "heading": heading,
                "content": content_to_save,
                "content_type": detect_content_type(content_to_save),
                "level": level,
                "order": base_order,
                "sub_order": sub_order,
                "parent_heading": section.get("parent_heading"),
                "parent_chunk_id": section.get("parent_chunk_id"),
                "parent_level": section.get("parent_level"),
                "hierarchy_path": section.get("hierarchy_path"),
            }
        )

    return chunks


def build_hierarchy_path(heading_stack: list, current_heading: str) -> str:
    """Build hierarchy path to current heading"""
    path_parts = [h[1] for h in heading_stack]
    if current_heading:
        path_parts.append(current_heading)
    return " > ".join(path_parts) if path_parts else ""


def calculate_siblings(chunks: list[dict]):
    """Link chunks with same parent_chunk_id as siblings"""
    from collections import defaultdict

    parent_groups = defaultdict(list)
    for chunk in chunks:
        parent_id = chunk.get("parent_chunk_id")
        chunk_id = chunk.get("id")
        if chunk_id and parent_id is not None:
            parent_groups[parent_id].append(chunk_id)

    for chunk in chunks:
        parent_id = chunk.get("parent_chunk_id")
        chunk_id = chunk.get("id")
        if parent_id is None:
            chunk["sibling_chunk_ids"] = []
        else:
            siblings = parent_groups.get(parent_id, [])
            chunk["sibling_chunk_ids"] = [s for s in siblings if s != chunk_id]


def chunk_markdown_semantic(markdown: str, document_id: str = "") -> list[dict]:
    """Semantic chunking with hierarchy metadata (Parent Document Retriever pattern)"""
    heading_chunks = chunk_markdown_by_heading(markdown)

    heading_stack = []
    final_chunks = []
    global_order = 0

    for section in heading_chunks:
        level = section["level"]
        heading = section["heading"]
        content_len = len(section["content"])

        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()

        parent_info = heading_stack[-1] if heading_stack else None

        chunk_id = (
            f"{document_id}_{global_order}" if document_id else f"chunk_{global_order}"
        )

        hierarchy_path = build_hierarchy_path(heading_stack, heading)

        section["id"] = chunk_id
        section["order"] = global_order
        section["parent_heading"] = parent_info[1] if parent_info else None
        section["parent_chunk_id"] = parent_info[2] if parent_info else None
        section["parent_level"] = parent_info[0] if parent_info else None
        section["hierarchy_path"] = hierarchy_path

        if content_len > MAX_CHUNK_SIZE:
            sub_chunks = split_by_paragraphs(section)
            for sub_chunk in sub_chunks:
                sub_chunk["id"] = (
                    f"{document_id}_{global_order}"
                    if document_id
                    else f"chunk_{global_order}"
                )
                sub_chunk["order"] = global_order
                final_chunks.append(sub_chunk)
                global_order += 1
            logger.info(
                f"  [Split] '{heading[:30]}...' ({content_len} chars) -> {len(sub_chunks)} chunks"
            )
        else:
            if "content_type" not in section:
                section["content_type"] = detect_content_type(section["content"])
            final_chunks.append(section)
            global_order += 1

        if level > 0:
            heading_stack.append((level, heading, chunk_id))

    calculate_siblings(final_chunks)

    logger.info(
        f"[Semantic chunking] {len(heading_chunks)} sections -> {len(final_chunks)} chunks (with hierarchy)"
    )
    return final_chunks


# =============================================================================
# Embedding Functions
# =============================================================================


def get_embeddings(
    texts: list[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
    *,
    batch_size: int = EMBEDDING_BATCH_SIZE,
    batch_sleep_seconds: float = EMBEDDING_BATCH_SLEEP_SECONDS,
    retry_sleep_seconds: float = EMBEDDING_RETRY_SLEEP_SECONDS,
    max_retries: int = EMBEDDING_MAX_RETRIES,
) -> list[list[float]]:
    """Generate embeddings with Gemini.

    Args:
        texts: List of texts to embed
        task_type: Gemini task type - "RETRIEVAL_DOCUMENT" for indexing,
                   "RETRIEVAL_QUERY" for search queries
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured")

    client = genai.Client(api_key=api_key)

    embeddings = []
    for batch_start in range(0, len(texts), batch_size):
        batch = texts[batch_start : batch_start + batch_size]

        for attempt in range(max_retries):
            try:
                result = client.models.embed_content(
                    model="gemini-embedding-001",
                    contents=batch,
                    config={"task_type": task_type},
                )
                embeddings.extend(embedding.values for embedding in result.embeddings)
                break
            except Exception as e:
                status_code = getattr(e, "status_code", None)
                is_retryable = status_code == 429 and attempt < max_retries - 1
                if not is_retryable:
                    raise

                logger.warning(
                    "[Embeddings] Batch %s hit 429, retrying in %.2fs (%s/%s)",
                    (batch_start // batch_size) + 1,
                    retry_sleep_seconds,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(retry_sleep_seconds)

        has_next_batch = batch_start + batch_size < len(texts)
        if has_next_batch and batch_sleep_seconds > 0:
            time.sleep(batch_sleep_seconds)

    logger.info(f"[Embeddings] {len(embeddings)} generated (task_type={task_type})")
    return embeddings


# =============================================================================
# Qdrant Functions
# =============================================================================


def init_qdrant_collection(
    client: QdrantClient, collection_name: str, force_recreate: bool = False
):
    """Initialize Qdrant collection (Dense + Sparse hybrid search)"""
    collections = client.get_collections().collections
    existing_names = [c.name for c in collections]

    needs_creation = collection_name not in existing_names

    if needs_creation or force_recreate:
        if force_recreate and collection_name in existing_names:
            client.delete_collection(collection_name)
            logger.info(f"[Qdrant] Deleted existing collection '{collection_name}'")

        client.create_collection(
            collection_name=collection_name,
            vectors_config={
                "dense": VectorParams(
                    size=EMBEDDING_DIMENSION, distance=Distance.COSINE
                )
            },
            sparse_vectors_config={"sparse": SparseVectorParams(modifier=Modifier.IDF)},
        )
        logger.info(
            f"[Qdrant] Created collection '{collection_name}' (Named vectors: dense + sparse)"
        )
    else:
        logger.info(f"[Qdrant] Collection '{collection_name}' already exists")


def store_in_qdrant(
    document_id: str,
    chunks: list[dict],
    dense_embeddings: list[list[float]],
    sparse_embeddings: list[tuple[list[int], list[float]]],
    metadata: dict,
    collection_name: str,
):
    """Store chunks in Qdrant (Dense + Sparse hybrid search)"""
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    init_qdrant_collection(client, collection_name)

    points = []
    for i, (chunk, dense_embedding, sparse_embedding) in enumerate(
        zip(chunks, dense_embeddings, sparse_embeddings, strict=False)
    ):
        point_id = str(uuid.uuid4())
        points.append(
            PointStruct(
                id=point_id,
                vector={
                    "dense": dense_embedding,
                    "sparse": SparseVector(
                        indices=sparse_embedding[0], values=sparse_embedding[1]
                    ),
                },
                payload={
                    "document_id": document_id,
                    "chunk_id": chunk.get("id", f"{document_id}_{i}"),
                    "heading": chunk["heading"],
                    "content": chunk["content"],
                    "level": chunk["level"],
                    "order": chunk["order"],
                    "original_filename": metadata["original_filename"],
                    "file_type": metadata["file_type"],
                    "parent_heading": chunk.get("parent_heading"),
                    "parent_chunk_id": chunk.get("parent_chunk_id"),
                    "parent_level": chunk.get("parent_level"),
                    "sibling_chunk_ids": chunk.get("sibling_chunk_ids", []),
                    "hierarchy_path": chunk.get("hierarchy_path"),
                    "content_type": chunk.get("content_type", "text"),
                    "contextual_description": chunk.get("contextual_description"),
                    "has_context": chunk.get("has_context", False),
                    "table_context": chunk.get("table_context"),
                },
            )
        )

    client.upsert(collection_name=collection_name, points=points)
    logger.info(
        f"[Qdrant] {len(points)} points stored (collection: {collection_name}, dense + sparse)"
    )


# =============================================================================
# API Endpoints
# =============================================================================


@router.post("/parse", response_model=IndexedDocument)
async def parse_document(
    current_company: dict = Depends(get_current_company),
    file: UploadFile = File(...),
    ocr: Literal["auto", "force", "skip"] = Query(
        "auto", description="OCR mode: auto, force, skip"
    ),
):
    """
    Parse document and chunk it, then index to Qdrant.
    Documents are stored per company in separate collections.
    Company is extracted from JWT token.

    **S3 Cache and Deduplication:**
    - Check for duplicates using file hash (SHA256)
    - If duplicate, load parse result from S3 (skip Upstage API)
    - If new, call Upstage API -> save to S3
    - Register in parsed_files table (global, reusable)
    - Register in documents table (per company, 1:N relationship)

    **Pipeline:**
    1. Calculate file hash -> check for duplicates
    2. Convert to markdown via Upstage Document Parse (on cache miss)
    3. Chunk by headings
    4. Generate Gemini embeddings
    5. Store in Qdrant (per company collection)
    6. Save to PostgreSQL (parsed_files + documents)
    """
    company = current_company["name"]
    collection_name = get_collection_name(company)

    if not file.filename:
        raise HTTPException(status_code=400, detail="File is required")

    file_type = get_file_type(file.filename)
    if not file_type:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Please upload PDF, image, or text files (md, txt, csv).",
        )

    file_bytes = await file.read()
    file_size = len(file_bytes)
    document_id = str(uuid.uuid4())

    logger.info("=" * 80)
    logger.info(f"[Document processing] ID: {document_id}, Company: {company}")
    logger.info(
        f"[File info] {file.filename}, type: {file_type}, size: {file_size} bytes"
    )
    logger.info(f"[Collection] {collection_name}")
    logger.info("=" * 80)

    parsed_file_id = None
    cache_hit = False

    if is_text_file(file_type):
        # Text files: read directly, no Upstage API / S3 cache / parsed_files needed
        logger.info("[Step 1] Text file - reading content directly (no Upstage API)")
        try:
            if file_type == "csv":
                markdown = csv_to_markdown(file_bytes)
            else:
                markdown = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=400,
                detail="File encoding error. Only UTF-8 encoded files are supported.",
            )

        if not markdown.strip():
            raise HTTPException(status_code=400, detail="File is empty")

        logger.info(
            f"[Step 1 complete] Text file loaded - markdown length: {len(markdown)} chars"
        )
    else:
        # PDF/image files: use hash-based cache. PDFs now use local table-aware parsing.
        file_hash = calculate_file_hash(file_bytes)
        logger.info(f"[File hash] {file_hash[:16]}...")

        logger.info("[Step 1] Checking for duplicates (parsed_files lookup)")
        parsed_file_repo = ParsedFileRepository()
        existing_parsed = await parsed_file_repo.get_by_hash(file_hash)

        if existing_parsed:
            cache_hit = True
            parsed_file_id = existing_parsed["id"]
            s3_key = existing_parsed["s3_key"]

            logger.info(
                f"[Cache hit] Reusing existing parse result (parsed_file_id={parsed_file_id})"
            )
            logger.info(f"[S3 load] {s3_key}")

            cached_data = load_from_s3(s3_key)
            if not cached_data:
                raise HTTPException(status_code=500, detail="S3 cache data not found")

            if file_type == "pdf" and not is_current_pdf_cache(cached_data):
                logger.info(
                    "[Cache stale] Existing PDF cache does not use table-aware parsing; reparsing"
                )
                cache_hit = False
                try:
                    parse_result = parse_pdf_with_table_aware_parser(
                        document_id=document_id,
                        filename=file.filename,
                        file_bytes=file_bytes,
                        file_size=file_size,
                        file_hash=file_hash,
                    )
                    markdown = parse_result["markdown"]
                    cache_data = parse_result["cache_data"]
                    save_to_s3(file_hash, cache_data)
                    save_parsed_cache(cache_data)
                    logger.info(
                        "[Step 1 complete] Stale PDF cache refreshed - "
                        f"markdown length: {len(markdown)} chars"
                    )
                except RuntimeError as e:
                    raise HTTPException(status_code=500, detail=str(e)) from e
            else:
                markdown = cached_data["markdown"]
                logger.info(
                    f"[Step 1 complete] S3 cache loaded - markdown length: {len(markdown)} chars"
                )
        else:
            logger.info("[Cache miss] Parsing required")
            logger.info("[Step 1.5] Starting document parsing")

            if file_type == "pdf":
                try:
                    parse_result = parse_pdf_with_table_aware_parser(
                        document_id=document_id,
                        filename=file.filename,
                        file_bytes=file_bytes,
                        file_size=file_size,
                        file_hash=file_hash,
                    )
                    markdown = parse_result["markdown"]
                    cache_data = parse_result["cache_data"]
                    logger.info(
                        "[Step 1.5 complete] Table-aware PDF parsing complete - "
                        f"markdown length: {len(markdown)} chars, "
                        f"table pages: {cache_data.get('general_parse', {}).get('table_page_count', 0)}"
                    )
                except RuntimeError as e:
                    raise HTTPException(status_code=500, detail=str(e)) from e
            else:
                # NOTE: Upstage remains enabled for image inputs. PDF Upstage fallback is intentionally disabled.
                api_key = os.getenv("UPSTAGE_API_KEY")
                if not api_key:
                    raise HTTPException(
                        status_code=500, detail="UPSTAGE_API_KEY not configured"
                    )
                result = await call_upstage_parse(
                    api_key=api_key,
                    filename=file.filename,
                    file_bytes=file_bytes,
                    ocr=ocr,
                )
                content_obj = result.get("content", {})
                markdown = content_obj.get("markdown", "")
                if not markdown:
                    raise HTTPException(
                        status_code=500,
                        detail="No markdown content returned from Upstage",
                    )
                cache_data = build_parse_cache_data(
                    document_id=document_id,
                    filename=file.filename,
                    file_type=file_type,
                    file_size=file_size,
                    markdown=markdown,
                    file_hash=file_hash,
                    parse_strategy="upstage_full",
                    upstage_response=result,
                )
                logger.info(
                    f"[Step 1.5 complete] Upstage parsing complete - markdown length: {len(markdown)} chars"
                )

            s3_key = save_to_s3(file_hash, cache_data)

            parsed_file = await parsed_file_repo.create(
                {
                    "file_hash": file_hash,
                    "s3_key": s3_key,
                    "original_filename": file.filename,
                    "file_type": file_type,
                    "file_size": file_size,
                }
            )
            parsed_file_id = parsed_file["id"]
            logger.info(f"[parsed_files registered] id={parsed_file_id}")

            save_parsed_cache(cache_data)

    logger.info(
        "[Step 2] Starting semantic chunking (Parent Document Retriever pattern)"
    )
    chunks = chunk_markdown_semantic(markdown, document_id=document_id)

    if not chunks:
        raise HTTPException(
            status_code=400, detail="No content chunks could be extracted"
        )

    logger.info(f"[Step 2 complete] {len(chunks)} chunks created (with hierarchy)")

    # Step 2.5: Generate contextual descriptions (Contextual Retrieval)
    logger.info(f"[Step 2.5] Generating contextual descriptions ({len(chunks)} chunks)")

    batch_size = 20
    for batch_start in range(0, len(chunks), batch_size):
        batch = chunks[batch_start : batch_start + batch_size]
        tasks = [
            generate_contextual_description(
                chunk_content=c["content"],
                full_markdown=markdown,
                hierarchy_path=c.get("hierarchy_path", ""),
            )
            for c in batch
        ]
        descriptions = await asyncio.gather(*tasks)
        for c, desc in zip(batch, descriptions, strict=True):
            c["contextual_description"] = desc

    logger.info(f"[Step 2.5 complete] {len(chunks)} contextual descriptions generated")

    logger.info("[Step 3] Generating Gemini embeddings")
    texts_to_embed = []
    for c in chunks:
        content = c["content"]
        contextual_desc = c.get("contextual_description", "")

        if contextual_desc:
            text_to_embed = f"{contextual_desc}\n\n{content}"
        else:
            text_to_embed = content
        texts_to_embed.append(text_to_embed)

    dense_embeddings = get_embeddings(texts_to_embed)
    logger.info(f"[Step 3 complete] {len(dense_embeddings)} dense embeddings generated")

    logger.info("[Step 3.5] Generating sparse embeddings")
    sparse_embeddings = get_sparse_embeddings(texts_to_embed)
    logger.info(
        f"[Step 3.5 complete] {len(sparse_embeddings)} sparse embeddings generated"
    )

    logger.info(f"[Step 4] Storing in Qdrant (collection: {collection_name})")
    metadata = {
        "original_filename": file.filename,
        "file_type": file_type,
    }
    store_in_qdrant(
        document_id,
        chunks,
        dense_embeddings,
        sparse_embeddings,
        metadata,
        collection_name,
    )
    logger.info("[Step 4 complete] Qdrant storage complete")

    logger.info("[Step 5] Storing in PostgreSQL")
    doc_repo = DocumentRepository()
    await doc_repo.create(
        {
            "id": UUID(document_id),
            "company_id": current_company["id"],
            "parsed_file_id": parsed_file_id,
            "original_filename": file.filename,
            "file_type": file_type,
            "file_size": file_size,
            "chunk_count": len(chunks),
        }
    )
    logger.info("[Step 5 complete] PostgreSQL storage complete")

    logger.info("=" * 80)
    logger.info(
        f"[Document processing complete] ID: {document_id}, chunks: {len(chunks)}, cache_hit: {cache_hit}"
    )
    logger.info("=" * 80)

    return IndexedDocument(
        document_id=document_id,
        original_filename=file.filename,
        file_type=file_type,
        file_size=file_size,
        chunk_count=len(chunks),
        chunks=[
            Chunk(
                id=c["id"],
                heading=c["heading"],
                content=c["content"],
                level=c["level"],
                order=c["order"],
                parent_heading=c.get("parent_heading"),
                parent_chunk_id=c.get("parent_chunk_id"),
                parent_level=c.get("parent_level"),
                sibling_chunk_ids=c.get("sibling_chunk_ids", []),
                hierarchy_path=c.get("hierarchy_path"),
                content_type=c.get("content_type", "text"),
                contextual_description=c.get("contextual_description"),
                has_context=c.get("has_context", False),
                table_context=c.get("table_context"),
            )
            for c in chunks
        ],
    )


def fetch_chunks_by_ids(
    client: QdrantClient, chunk_ids: list[str], collection_name: str
) -> dict[str, dict]:
    """Fetch chunks by chunk_id list and return as dictionary"""
    if not chunk_ids:
        return {}

    results = client.scroll(
        collection_name=collection_name,
        scroll_filter={
            "should": [
                {"key": "chunk_id", "match": {"value": cid}} for cid in chunk_ids
            ]
        },
        limit=len(chunk_ids) + 10,
        with_payload=True,
        with_vectors=False,
    )

    chunks = {}
    for point in results[0]:
        payload = point.payload
        chunk_id = payload.get("chunk_id")
        if chunk_id:
            chunks[chunk_id] = {
                "chunk_id": chunk_id,
                "heading": payload.get("heading"),
                "content": payload.get("content"),
                "level": payload.get("level"),
                "order": payload.get("order"),
                "hierarchy_path": payload.get("hierarchy_path"),
            }
    return chunks


def fetch_chunk_with_context(
    client: QdrantClient, chunk_id: str, document_id: str, collection_name: str
) -> dict:
    """Fetch chunk with parent/sibling chunks by chunk_id"""
    results = client.scroll(
        collection_name=collection_name,
        scroll_filter={"must": [{"key": "chunk_id", "match": {"value": chunk_id}}]},
        limit=1,
        with_payload=True,
        with_vectors=False,
    )

    if not results[0]:
        return None

    payload = results[0][0].payload

    related_ids = set()
    parent_chunk_id = payload.get("parent_chunk_id")
    sibling_chunk_ids = payload.get("sibling_chunk_ids", [])

    if parent_chunk_id:
        related_ids.add(parent_chunk_id)
    related_ids.update(sibling_chunk_ids)

    related_chunks = fetch_chunks_by_ids(client, list(related_ids), collection_name)

    return {
        "chunk_id": chunk_id,
        "heading": payload.get("heading"),
        "content": payload.get("content"),
        "level": payload.get("level"),
        "order": payload.get("order"),
        "hierarchy_path": payload.get("hierarchy_path"),
        "parent_chunk_id": parent_chunk_id,
        "parent": related_chunks.get(parent_chunk_id) if parent_chunk_id else None,
        "siblings": [
            related_chunks.get(sid)
            for sid in sibling_chunk_ids
            if related_chunks.get(sid)
        ],
    }


@router.get("/search")
async def search_documents(
    current_company: dict = Depends(get_current_company),
    query: str = Query(..., description="Search query"),
    limit: int = Query(5, description="Number of results"),
    include_context: bool = Query(False, description="Include parent/sibling chunks"),
):
    """Hybrid search: Dense (0.7) + Sparse (0.3) + Cohere Rerank. Company from JWT token."""
    company = current_company["name"]
    collection_name = get_collection_name(company)
    logger.info("=" * 80)
    logger.info(f"[Hybrid search] Company: {company}, Collection: {collection_name}")
    logger.info(
        f"[Search params] Query: '{query}', limit: {limit}, include_context: {include_context}"
    )

    # Use shared hybrid search function
    from core.shared.vector_search import hybrid_search

    search_results = hybrid_search(query, company, limit=limit)

    if not search_results:
        logger.info("=" * 80)
        return []

    # Build results (with optional context enrichment)
    if include_context:
        qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        results = []
        for r in search_results:
            chunk_id = r.get("chunk_id")
            document_id = r.get("document_id")

            if chunk_id and document_id:
                chunk_with_context = fetch_chunk_with_context(
                    qdrant, chunk_id, document_id, collection_name
                )
                if chunk_with_context:
                    chunk_with_context["relevance_score"] = r.get("relevance_score")
                    chunk_with_context["dense_score"] = r.get("dense_score")
                    chunk_with_context["sparse_score"] = r.get("sparse_score")
                    chunk_with_context["hybrid_score"] = r.get("hybrid_score")
                    results.append(chunk_with_context)
                    continue

            results.append(
                {
                    "content": r["content"],
                    "metadata": r,
                }
            )
    else:
        results = [{"content": r["content"], "metadata": r} for r in search_results]

    logger.info(f"[Search complete] {len(results)} results returned")
    logger.info("=" * 80)
    return results


@router.get("/list")
async def list_documents(current_company: dict = Depends(get_current_company)):
    """List documents for a company. Company from JWT token."""
    company = current_company["name"]
    doc_repo = DocumentRepository()
    documents = await doc_repo.get_by_company(current_company["id"])

    return {
        "company": company,
        "company_id": str(current_company["id"]),
        "count": len(documents),
        "documents": [
            {
                "id": str(d["id"]),
                "original_filename": d["original_filename"],
                "file_type": d["file_type"],
                "file_size": d["file_size"],
                "chunk_count": d["chunk_count"],
                "parsed_at": d["parsed_at"].isoformat() if d["parsed_at"] else None,
                "status": d["status"],
                "created_at": d["created_at"].isoformat() if d["created_at"] else None,
            }
            for d in documents
        ],
    }


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    current_company: dict = Depends(get_current_company),
):
    """Get all chunks for a document_id and combine them. Company from JWT token."""
    company = current_company["name"]
    collection_name = get_collection_name(company)
    qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    results = qdrant.scroll(
        collection_name=collection_name,
        scroll_filter={
            "must": [{"key": "document_id", "match": {"value": document_id}}]
        },
        limit=1000,
        with_payload=True,
        with_vectors=False,
    )

    points = results[0]

    if not points:
        raise HTTPException(status_code=404, detail="Document not found")

    sorted_points = sorted(points, key=lambda p: p.payload.get("order", 0))

    first_payload = sorted_points[0].payload
    original_filename = first_payload.get("original_filename", "")
    file_type = first_payload.get("file_type", "")

    chunks = [
        {
            "id": p.payload.get("chunk_id"),
            "heading": p.payload.get("heading"),
            "content": p.payload.get("content"),
            "level": p.payload.get("level"),
            "order": p.payload.get("order"),
            "parent_heading": p.payload.get("parent_heading"),
            "parent_chunk_id": p.payload.get("parent_chunk_id"),
            "parent_level": p.payload.get("parent_level"),
            "sibling_chunk_ids": p.payload.get("sibling_chunk_ids", []),
            "hierarchy_path": p.payload.get("hierarchy_path"),
        }
        for p in sorted_points
    ]

    full_markdown = ""
    for chunk in chunks:
        heading = chunk["heading"]
        level = chunk["level"]
        content = chunk["content"]

        if level > 0:
            full_markdown += f"{'#' * level} {heading}\n\n"
        full_markdown += f"{content}\n\n"

    return {
        "document_id": document_id,
        "original_filename": original_filename,
        "file_type": file_type,
        "chunk_count": len(chunks),
        "chunks": chunks,
        "full_markdown": full_markdown.strip(),
    }


@router.get("/cache/{document_id}")
async def get_cached_document(
    document_id: str,
    current_company: dict = Depends(get_current_company),
):
    """
    Get cached original parse result (including Upstage API response).
    Company from JWT token.

    Lookup order:
    1. Get parsed_file_id from documents table
    2. Get S3 key from parsed_files table
    3. Load from S3
    4. Fallback: local cache lookup
    """
    doc_repo = DocumentRepository()
    doc = await doc_repo.get_by_id(UUID(document_id))

    if doc and doc.get("parsed_file_id"):
        parsed_file_repo = ParsedFileRepository()
        parsed_file = await parsed_file_repo.get_by_id(doc["parsed_file_id"])

        if parsed_file:
            cached = load_from_s3(parsed_file["s3_key"])
            if cached:
                return cached

    cached = load_parsed_cache(document_id)
    if cached:
        return cached

    raise HTTPException(status_code=404, detail="Cached document not found")


@router.get("/cache")
async def list_cached_documents(current_company: dict = Depends(get_current_company)):
    """
    List all parsed documents (based on parsed_files table).
    Company from JWT token (unused but required for auth).

    Returns global parse cache stored in S3.
    """
    parsed_file_repo = ParsedFileRepository()
    parsed_files = await parsed_file_repo.get_all()

    documents = [
        {
            "parsed_file_id": str(pf["id"]),
            "file_hash": pf["file_hash"],
            "original_filename": pf["original_filename"],
            "file_type": pf["file_type"],
            "file_size": pf["file_size"],
            "s3_key": pf["s3_key"],
            "created_at": pf["created_at"].isoformat() if pf["created_at"] else None,
        }
        for pf in parsed_files
    ]

    local_cache_files = list(CACHE_DIR.glob("*.json"))
    local_documents = []

    for cache_file in local_cache_files:
        try:
            with open(cache_file, encoding="utf-8") as f:
                data = json.load(f)
                local_documents.append(
                    {
                        "document_id": data.get("document_id"),
                        "original_filename": data.get("original_filename"),
                        "file_type": data.get("file_type"),
                        "file_size": data.get("file_size"),
                        "parsed_at": data.get("parsed_at"),
                        "markdown_length": len(data.get("markdown", "")),
                        "source": "local_cache",
                    }
                )
        except Exception as e:
            logger.warning(f"Failed to read cache file: {cache_file}, {e}")

    return {
        "count": len(documents),
        "s3_cached": documents,
        "local_cached": local_documents,
        "local_count": len(local_documents),
    }


@router.post("/reindex/{document_id}")
async def reindex_document(
    document_id: str,
    current_company: dict = Depends(get_current_company),
):
    """
    Reindex cached document with new chunking strategy.
    Company from JWT token.

    Lookup order:
    1. Get parsed_file_id from documents table
    2. Get S3 key from parsed_files table
    3. Load from S3
    4. Fallback: local cache lookup
    """
    company = current_company["name"]
    collection_name = get_collection_name(company)
    logger.info("=" * 80)
    logger.info(
        f"[Reindexing] Document ID: {document_id}, Company: {company}, Collection: {collection_name}"
    )

    cached = None
    parsed_file_id = None

    doc_repo = DocumentRepository()
    doc = await doc_repo.get_by_id(UUID(document_id))

    if doc and doc.get("parsed_file_id"):
        parsed_file_repo = ParsedFileRepository()
        parsed_file = await parsed_file_repo.get_by_id(doc["parsed_file_id"])

        if parsed_file:
            cached = load_from_s3(parsed_file["s3_key"])
            parsed_file_id = parsed_file["id"]
            logger.info(f"[Step 1] S3 cache loaded (parsed_file_id={parsed_file_id})")

    if not cached:
        cached = load_parsed_cache(document_id)
        if cached:
            logger.info("[Step 1] Using local cache fallback")

    if not cached:
        raise HTTPException(status_code=404, detail="Document not found in cache")

    markdown = cached["markdown"]
    logger.info(f"[Step 1] Cache loaded - markdown length: {len(markdown)} chars")

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    collections = client.get_collections().collections
    collection_exists = any(c.name == collection_name for c in collections)

    old_count = 0
    if collection_exists:
        old_points = client.scroll(
            collection_name=collection_name,
            scroll_filter={
                "must": [{"key": "document_id", "match": {"value": document_id}}]
            },
            limit=1000,
            with_payload=False,
            with_vectors=False,
        )
        old_count = len(old_points[0])

        if old_count > 0:
            client.delete(
                collection_name=collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="document_id", match=MatchValue(value=document_id)
                        )
                    ]
                ),
            )
        logger.info(
            f"[Step 2] Deleted {old_count} existing points (collection: {collection_name})"
        )
    else:
        logger.info(
            f"[Step 2] Collection '{collection_name}' doesn't exist - will be created"
        )

    chunks = chunk_markdown_semantic(markdown, document_id=document_id)

    if not chunks:
        raise HTTPException(
            status_code=400, detail="No content chunks could be extracted"
        )

    logger.info(
        f"[Step 3] New chunking complete - {len(chunks)} chunks (with hierarchy)"
    )

    # Step 3.5: Generate contextual descriptions (Contextual Retrieval)
    logger.info(f"[Step 3.5] Generating contextual descriptions ({len(chunks)} chunks)")

    batch_size = 20
    for batch_start in range(0, len(chunks), batch_size):
        batch = chunks[batch_start : batch_start + batch_size]
        tasks = [
            generate_contextual_description(
                chunk_content=c["content"],
                full_markdown=markdown,
                hierarchy_path=c.get("hierarchy_path", ""),
            )
            for c in batch
        ]
        descriptions = await asyncio.gather(*tasks)
        for c, desc in zip(batch, descriptions, strict=True):
            c["contextual_description"] = desc

    logger.info(f"[Step 3.5 complete] {len(chunks)} contextual descriptions generated")

    texts_to_embed = []
    for c in chunks:
        content = c["content"]
        contextual_desc = c.get("contextual_description", "")

        if contextual_desc:
            text_to_embed = f"{contextual_desc}\n\n{content}"
        else:
            text_to_embed = content
        texts_to_embed.append(text_to_embed)

    dense_embeddings = get_embeddings(texts_to_embed)
    logger.info(f"[Step 4] Dense embeddings generated - {len(dense_embeddings)}")

    logger.info("[Step 4.5] Generating sparse embeddings")
    sparse_embeddings = get_sparse_embeddings(texts_to_embed)
    logger.info(
        f"[Step 4.5 complete] {len(sparse_embeddings)} sparse embeddings generated"
    )

    metadata = {
        "original_filename": cached["original_filename"],
        "file_type": cached["file_type"],
    }
    store_in_qdrant(
        document_id,
        chunks,
        dense_embeddings,
        sparse_embeddings,
        metadata,
        collection_name,
    )
    logger.info(f"[Step 5] Qdrant storage complete (collection: {collection_name})")

    logger.info("[Step 6] PostgreSQL upsert")
    doc_repo = DocumentRepository()
    await doc_repo.upsert(
        {
            "id": UUID(document_id),
            "company_id": current_company["id"],
            "original_filename": cached["original_filename"],
            "file_type": cached["file_type"],
            "file_size": cached["file_size"],
            "chunk_count": len(chunks),
        }
    )
    logger.info("[Step 6 complete] PostgreSQL upsert complete")

    logger.info("=" * 80)
    logger.info(f"[Reindexing complete] {old_count} -> {len(chunks)} chunks")
    logger.info("=" * 80)

    return {
        "document_id": document_id,
        "old_chunk_count": old_count,
        "new_chunk_count": len(chunks),
        "chunks": [
            {
                "id": c["id"],
                "heading": c["heading"],
                "content_length": len(c["content"]),
                "level": c["level"],
                "order": c["order"],
                "parent_heading": c.get("parent_heading"),
                "parent_chunk_id": c.get("parent_chunk_id"),
                "parent_level": c.get("parent_level"),
                "sibling_chunk_ids": c.get("sibling_chunk_ids", []),
                "hierarchy_path": c.get("hierarchy_path"),
                "content_type": c.get("content_type", "text"),
                "contextual_description": c.get("contextual_description"),
                "has_context": c.get("has_context", False),
            }
            for c in chunks
        ],
    }


# ============================================================================
# Company & Document Management Endpoints
# ============================================================================


@router.get("/companies", tags=["companies"])
async def list_companies():
    """List all companies"""
    company_repo = CompanyRepository()
    companies = await company_repo.get_all()

    return {
        "count": len(companies),
        "companies": [
            {
                "id": str(c["id"]),
                "name": c["name"],
                "display_name": c["display_name"],
                "created_at": c["created_at"].isoformat() if c["created_at"] else None,
                "updated_at": c["updated_at"].isoformat() if c["updated_at"] else None,
            }
            for c in companies
        ],
    }


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    current_company: dict = Depends(get_current_company),
):
    """
    Delete a single document (PostgreSQL hard delete + Qdrant delete).
    Company from JWT token.

    1. Verify document belongs to company
    2. Delete Qdrant points for document_id
    3. Hard delete document in PostgreSQL
    """
    company = current_company["name"]
    collection_name = get_collection_name(company)
    logger.info("=" * 80)
    logger.info(f"[Delete document] ID: {document_id}, Company: {company}")

    # Step 1: Verify document exists and belongs to company
    doc_repo = DocumentRepository()
    document = await doc_repo.get_by_id(UUID(document_id))

    if not document:
        raise HTTPException(
            status_code=404, detail=f"Document '{document_id}' not found"
        )

    if document["company_id"] != current_company["id"]:
        raise HTTPException(
            status_code=403, detail="Document does not belong to this company"
        )

    logger.info(f"[Step 1] Document verified: {document['original_filename']}")

    # Step 2: Delete from Qdrant
    qdrant_deleted = 0
    try:
        client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

        collections = client.get_collections().collections
        collection_exists = any(c.name == collection_name for c in collections)

        if collection_exists:
            # Count existing points
            points_before = client.scroll(
                collection_name=collection_name,
                scroll_filter={
                    "must": [{"key": "document_id", "match": {"value": document_id}}]
                },
                limit=1000,
                with_payload=False,
                with_vectors=False,
            )
            qdrant_deleted = len(points_before[0])

            if qdrant_deleted > 0:
                client.delete(
                    collection_name=collection_name,
                    points_selector=Filter(
                        must=[
                            FieldCondition(
                                key="document_id", match=MatchValue(value=document_id)
                            )
                        ]
                    ),
                )
                logger.info(f"[Step 2] Qdrant: {qdrant_deleted} points deleted")
        else:
            logger.info(f"[Step 2] Collection '{collection_name}' doesn't exist")

    except Exception as e:
        logger.warning(f"[Qdrant delete failed] {e}")

    # Step 3: Hard delete in PostgreSQL
    deleted = await doc_repo.delete(UUID(document_id))
    logger.info(f"[Step 3] PostgreSQL: delete {'successful' if deleted else 'failed'}")

    logger.info("=" * 80)
    logger.info(
        f"[Delete complete] Document: {document_id}, Qdrant points: {qdrant_deleted}"
    )
    logger.info("=" * 80)

    return {
        "document_id": document_id,
        "status": "deleted",
        "qdrant_points_deleted": qdrant_deleted,
        "original_filename": document["original_filename"],
    }


@router.delete("/all")
async def delete_all_documents(current_company: dict = Depends(get_current_company)):
    """
    Delete all documents for a company (PostgreSQL hard delete + Qdrant delete).
    Company from JWT token.

    1. Get all document_ids for the company
    2. Delete Qdrant points for each document_id
    3. Hard delete all documents in PostgreSQL
    """
    company = current_company["name"]
    company_id = current_company["id"]
    collection_name = get_collection_name(company)
    logger.info("=" * 80)
    logger.info(f"[Delete all documents] Company: {company}")
    logger.info(f"[Step 1] Company: {company} (ID: {company_id})")

    doc_repo = DocumentRepository()
    document_ids = await doc_repo.get_all_ids_by_company(company_id)

    if not document_ids:
        logger.info("[Step 2] No documents to delete")
        return {
            "company": company,
            "status": "no_documents",
            "documents_deleted": 0,
            "qdrant_points_deleted": 0,
        }

    logger.info(f"[Step 2] {len(document_ids)} documents to delete")

    total_qdrant_deleted = 0
    try:
        client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

        collections = client.get_collections().collections
        collection_exists = any(c.name == collection_name for c in collections)

        if collection_exists:
            for doc_id in document_ids:
                doc_id_str = str(doc_id)

                points_before = client.scroll(
                    collection_name=collection_name,
                    scroll_filter={
                        "must": [{"key": "document_id", "match": {"value": doc_id_str}}]
                    },
                    limit=1000,
                    with_payload=False,
                    with_vectors=False,
                )
                points_count = len(points_before[0])

                if points_count > 0:
                    client.delete(
                        collection_name=collection_name,
                        points_selector=Filter(
                            must=[
                                FieldCondition(
                                    key="document_id",
                                    match=MatchValue(value=doc_id_str),
                                )
                            ]
                        ),
                    )
                    total_qdrant_deleted += points_count
                    logger.info(
                        f"  - Document {doc_id_str}: {points_count} points deleted"
                    )

        logger.info(f"[Step 3] Qdrant: {total_qdrant_deleted} total points deleted")
    except Exception as e:
        logger.warning(f"[Qdrant delete failed] {e}")

    deleted_count = await doc_repo.delete_all_by_company(company_id)
    logger.info(f"[Step 4] PostgreSQL: {deleted_count} documents deleted")

    logger.info("=" * 80)
    logger.info(
        f"[Delete complete] Company: {company}, Documents: {deleted_count}, Qdrant points: {total_qdrant_deleted}"
    )
    logger.info("=" * 80)

    return {
        "company": company,
        "status": "deleted",
        "documents_deleted": deleted_count,
        "qdrant_points_deleted": total_qdrant_deleted,
        "document_ids": [str(doc_id) for doc_id in document_ids],
    }
