"""Chunk domain models for RAG corpus (Phase 8 + 9)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class ChunkMetadata:
    doc_type: str  # 'issue' | 'docs'
    doc_id: str  # issue number or doc filename
    chunk_role: str  # 'parent' | 'child'
    closed_at: str | None = None  # ISO 8601, issues only
    labels: list[str] = field(default_factory=list)
    repo_path: str | None = None  # file path, docs only


@dataclass
class ChunkRecord:
    id: uuid.UUID
    parent_id: uuid.UUID | None  # None for parent chunks
    doc_id: str
    doc_type: str
    chunk_role: str
    content: str
    content_hash: str
    metadata: ChunkMetadata
    embedding: list[float] | None = None  # populated in embed stage


@dataclass
class RetrievedChunk:
    """A ranked retrieval result: child chunk matched + its parent for context."""

    child: ChunkRecord
    parent: ChunkRecord | None  # full issue text; None if parent not found
    score: float  # cross-encoder score (higher = more relevant)


@dataclass
class IngestStats:
    issues_loaded: int = 0
    parents_created: int = 0
    children_created: int = 0
    children_embedded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
