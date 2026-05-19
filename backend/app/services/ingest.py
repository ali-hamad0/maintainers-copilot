"""RAG corpus ingestion pipeline.

Stages: load -> clean -> chunk -> embed -> store.

Each resolved issue from rag_corpus.jsonl becomes:
  1 parent chunk  — full title+body up to 2000 chars; NOT embedded; returned as context.
  N child chunks  — 256-char slices with 32-char overlap; embedded with gemini-embedding-001.

Idempotent: rows are skipped when content_hash already exists in the chunks table.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid

import structlog
from minio import Minio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.chunk import ChunkMetadata, ChunkRecord, IngestStats
from app.infra.embedder import Embedder

log = structlog.get_logger(__name__)

_CORPUS_KEY = "splits/v1/rag_corpus.jsonl"
_PARENT_MAX_CHARS = 2_000
_CHILD_SIZE = 256
_CHILD_OVERLAP = 32


class IngestService:
    """Ingests rag_corpus.jsonl from MinIO into the pgvector chunks table."""

    def __init__(self, embedder: Embedder, minio_client: Minio, bucket: str) -> None:
        self._embedder = embedder
        self._minio = minio_client
        self._bucket = bucket

    # ── Stage 1: Load ────────────────────────────────────────────────────────

    def _load(self) -> list[dict[str, object]]:
        response = self._minio.get_object(self._bucket, _CORPUS_KEY)
        raw = response.read()
        issues = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        log.info("ingest.loaded", count=len(issues))
        return issues

    # ── Stage 2: Clean ───────────────────────────────────────────────────────

    @staticmethod
    def _clean(raw: str) -> str:
        raw = re.sub(r"```[a-z]*\n", "```\n", raw)  # normalise code-fence language tags
        raw = re.sub(r"\n{3,}", "\n\n", raw)  # collapse excessive blank lines
        return raw.strip()

    # ── Stage 3: Chunk ───────────────────────────────────────────────────────

    def _make_parent(self, issue: dict[str, object]) -> ChunkRecord:
        content = self._clean(str(issue.get("text") or ""))[:_PARENT_MAX_CHARS]
        doc_id = str(issue["id"])
        parent_id = uuid.uuid4()
        label = str(issue.get("label") or "")
        closed_at = str(issue["closed_at"]) if issue.get("closed_at") else None
        return ChunkRecord(
            id=parent_id,
            parent_id=None,
            doc_id=doc_id,
            doc_type="issue",
            chunk_role="parent",
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            metadata=ChunkMetadata(
                doc_type="issue",
                doc_id=doc_id,
                chunk_role="parent",
                closed_at=closed_at,
                labels=[label] if label else [],
            ),
        )

    def _make_children(self, parent: ChunkRecord) -> list[ChunkRecord]:
        children: list[ChunkRecord] = []
        text = parent.content
        start = 0
        while start < len(text):
            snippet = text[start : start + _CHILD_SIZE]
            children.append(
                ChunkRecord(
                    id=uuid.uuid4(),
                    parent_id=parent.id,
                    doc_id=parent.doc_id,
                    doc_type="issue",
                    chunk_role="child",
                    content=snippet,
                    content_hash=hashlib.sha256(snippet.encode()).hexdigest(),
                    metadata=ChunkMetadata(
                        doc_type="issue",
                        doc_id=parent.doc_id,
                        chunk_role="child",
                        closed_at=parent.metadata.closed_at,
                        labels=parent.metadata.labels,
                    ),
                )
            )
            start += _CHILD_SIZE - _CHILD_OVERLAP
        return children

    # ── Stage 4: Embed ───────────────────────────────────────────────────────

    async def _embed(
        self, pairs: list[tuple[ChunkRecord, list[ChunkRecord]]]
    ) -> list[tuple[ChunkRecord, list[ChunkRecord]]]:
        children_flat = [child for _, children in pairs for child in children]
        texts = [c.content for c in children_flat]
        log.info("ingest.embedding", child_count=len(texts))
        vectors = await self._embedder.embed_batch(texts)

        idx = 0
        result: list[tuple[ChunkRecord, list[ChunkRecord]]] = []
        for parent, children in pairs:
            for child in children:
                child.embedding = vectors[idx]
                idx += 1
            result.append((parent, children))
        return result

    # ── Stage 5: Store ───────────────────────────────────────────────────────

    async def _store(
        self,
        pairs: list[tuple[ChunkRecord, list[ChunkRecord]]],
        session: AsyncSession,
    ) -> tuple[int, int]:
        inserted = skipped = 0

        for parent, children in pairs:
            for chunk in [parent, *children]:
                row = await session.execute(
                    text("SELECT 1 FROM chunks WHERE content_hash = :h"),
                    {"h": chunk.content_hash},
                )
                if row.scalar() is not None:
                    skipped += 1
                    continue

                embedding_str = (
                    f"[{','.join(str(v) for v in chunk.embedding)}]" if chunk.embedding else None
                )
                await session.execute(
                    text(
                        "INSERT INTO chunks "
                        "(id, parent_id, doc_id, doc_type, chunk_role, "
                        " content, content_hash, doc_metadata, embedding) "
                        "VALUES (:id, :parent_id, :doc_id, :doc_type, :chunk_role, "
                        "        :content, :content_hash, :doc_metadata, "
                        "        CAST(:embedding AS vector))"
                    ),
                    {
                        "id": str(chunk.id),
                        "parent_id": str(chunk.parent_id) if chunk.parent_id else None,
                        "doc_id": chunk.doc_id,
                        "doc_type": chunk.doc_type,
                        "chunk_role": chunk.chunk_role,
                        "content": chunk.content,
                        "content_hash": chunk.content_hash,
                        "doc_metadata": json.dumps(
                            {
                                "closed_at": chunk.metadata.closed_at,
                                "labels": chunk.metadata.labels,
                                "repo_path": chunk.metadata.repo_path,
                            }
                        ),
                        "embedding": embedding_str,
                    },
                )
                inserted += 1

        await session.commit()
        return inserted, skipped

    # ── Orchestrator ─────────────────────────────────────────────────────────

    async def run(self, session: AsyncSession) -> IngestStats:
        stats = IngestStats()

        issues = await asyncio.to_thread(self._load)
        stats.issues_loaded = len(issues)

        pairs: list[tuple[ChunkRecord, list[ChunkRecord]]] = []
        for issue in issues:
            parent = self._make_parent(issue)
            children = self._make_children(parent)
            pairs.append((parent, children))

        stats.parents_created = len(pairs)
        stats.children_created = sum(len(c) for _, c in pairs)

        pairs = await self._embed(pairs)
        stats.children_embedded = stats.children_created

        stats.rows_inserted, stats.rows_skipped = await self._store(pairs, session)

        log.info(
            "ingest.complete",
            issues=stats.issues_loaded,
            parents=stats.parents_created,
            children=stats.children_created,
            inserted=stats.rows_inserted,
            skipped=stats.rows_skipped,
        )
        return stats
