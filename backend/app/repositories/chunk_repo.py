"""SQL retrieval operations for the chunks table.

Repository layer — SQL only.  No HTTP calls, no caching, no LLM calls.
Conversion from raw DB rows / ORM objects to domain models happens here.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.chunk import ChunkMetadata, ChunkRecord
from app.repositories.orm_models import Chunk as OrmChunk

log = structlog.get_logger(__name__)

# Whitelist of allowed metadata filter keys with their SQL fragment and bind-param name.
# Values are always parameterized — only key names come from this dict (safe).
_FILTER_MAP: dict[str, tuple[str, str]] = {
    "doc_type": ("doc_type = :f_doc_type", "f_doc_type"),
    "closed_at_gte": (
        "doc_metadata->>'closed_at' >= :f_closed_at_gte",
        "f_closed_at_gte",
    ),
    "closed_at_lte": (
        "doc_metadata->>'closed_at' <= :f_closed_at_lte",
        "f_closed_at_lte",
    ),
    # @> containment check: labels JSON array contains the given string element
    "labels": (
        "doc_metadata->'labels' @> jsonb_build_array(:f_label)",
        "f_label",
    ),
}


def _build_filter_sql(filters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return (WHERE fragment starting with ' AND ...', bound params)."""
    unknown = set(filters) - set(_FILTER_MAP)
    if unknown:
        raise ValueError(f"Unknown filter key(s): {unknown!r}")

    clauses: list[str] = []
    params: dict[str, Any] = {}

    for key, value in filters.items():
        clause, param_name = _FILTER_MAP[key]
        clauses.append(clause)
        params[param_name] = value

    fragment = (" AND " + " AND ".join(clauses)) if clauses else ""
    return fragment, params


def _row_to_record(row: Any) -> ChunkRecord:
    """Convert a raw SQL Row or ORM Chunk instance to a ChunkRecord domain object."""
    meta: dict[str, Any] = row.doc_metadata or {}
    parent_id: uuid.UUID | None = row.parent_id
    return ChunkRecord(
        id=row.id,
        parent_id=parent_id,
        doc_id=row.doc_id,
        doc_type=row.doc_type,
        chunk_role=row.chunk_role,
        content=row.content,
        content_hash=row.content_hash,
        metadata=ChunkMetadata(
            doc_type=row.doc_type,
            doc_id=row.doc_id,
            chunk_role=row.chunk_role,
            closed_at=meta.get("closed_at"),
            labels=list(meta.get("labels") or []),
            repo_path=meta.get("repo_path"),
        ),
    )


class ChunkRepo:
    """SQL-only retrieval against the chunks table.

    One instance per application process; injected into RetrievalService.
    Session is passed per-call so it lives within a single request transaction.
    """

    async def sparse_search(
        self,
        session: AsyncSession,
        query: str,
        filters: dict[str, Any],
        limit: int,
    ) -> list[ChunkRecord]:
        """BM25-style ranking via Postgres ts_rank + websearch_to_tsquery.

        websearch_to_tsquery handles special characters and boolean operators
        gracefully (never raises a syntax error on arbitrary user input).
        """
        filter_sql, params = _build_filter_sql(filters)
        sql = text(
            "SELECT id, parent_id, doc_id, doc_type, chunk_role, "
            "       content, content_hash, doc_metadata "
            "FROM chunks "
            "WHERE chunk_role = 'child' "
            "  AND to_tsvector('english', content) "
            "      @@ websearch_to_tsquery('english', :query) "
            f"  {filter_sql} "
            "ORDER BY ts_rank("
            "    to_tsvector('english', content), "
            "    websearch_to_tsquery('english', :query)"
            ") DESC "
            "LIMIT :limit"
        )
        result = await session.execute(sql, {"query": query, "limit": limit, **params})
        rows = result.fetchall()
        log.debug("chunk_repo.sparse_search", query_len=len(query), hits=len(rows))
        return [_row_to_record(r) for r in rows]

    async def dense_search(
        self,
        session: AsyncSession,
        query_vec: list[float],
        filters: dict[str, Any],
        limit: int,
    ) -> list[ChunkRecord]:
        """Cosine similarity search via pgvector <=> operator (HNSW index)."""
        filter_sql, params = _build_filter_sql(filters)
        embedding_str = f"[{','.join(str(v) for v in query_vec)}]"
        sql = text(
            "SELECT id, parent_id, doc_id, doc_type, chunk_role, "
            "       content, content_hash, doc_metadata "
            "FROM chunks "
            "WHERE chunk_role = 'child' "
            "  AND embedding IS NOT NULL "
            f"  {filter_sql} "
            "ORDER BY embedding <=> CAST(:embedding AS vector) "
            "LIMIT :limit"
        )
        result = await session.execute(sql, {"embedding": embedding_str, "limit": limit, **params})
        rows = result.fetchall()
        log.debug("chunk_repo.dense_search", hits=len(rows))
        return [_row_to_record(r) for r in rows]

    async def fetch_parents(
        self,
        session: AsyncSession,
        parent_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, ChunkRecord]:
        """Fetch parent chunks by primary key for context window expansion."""
        if not parent_ids:
            return {}
        result = await session.execute(select(OrmChunk).where(OrmChunk.id.in_(parent_ids)))
        orm_rows = result.scalars().all()
        return {row.id: _row_to_record(row) for row in orm_rows}
