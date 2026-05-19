"""SQL repository for long-term (pgvector) memory.

Responsibilities:
  - INSERT into memory_long_term with embedding + provenance
  - INSERT audit_log row on every write (CLAUDE.md §7)
  - SELECT similar rows via pgvector cosine operator (<=>)

No HTTP errors, no cache logic — pure SQL.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.orm_models import AuditLog, MemoryLongTerm

log = structlog.get_logger(__name__)


class MemoryRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        content: str,
        embedding: list[float],
        source_tool: str,
        trust_score: float,
        payload: dict[str, Any],
        provenance: dict[str, Any],
        request_id: str | None = None,
    ) -> MemoryLongTerm:
        """Write one memory row and a paired audit_log row atomically."""
        memory = MemoryLongTerm(
            id=uuid.uuid4(),
            user_id=user_id,
            conversation_id=conversation_id,
            content=content,
            source_tool=source_tool,
            trust_score=trust_score,
            payload=payload,
            provenance=provenance,
        )
        self._session.add(memory)

        # Flush to get the row id before committing (needed for audit target_id).
        await self._session.flush()

        # Set the pgvector embedding via raw SQL (SQLAlchemy has no vector type yet).
        vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
        await self._session.execute(
            text(
                "UPDATE memory_long_term "
                "SET embedding = CAST(:vec AS vector) "
                "WHERE id = :row_id"
            ),
            {"vec": vec_str, "row_id": str(memory.id)},
        )

        # Paired audit log row (CLAUDE.md §7).
        audit = AuditLog(
            id=uuid.uuid4(),
            actor_id=user_id,
            action="memory.write",
            target_type="memory_long_term",
            target_id=str(memory.id),
            timestamp=datetime.now(UTC),
            request_id=request_id,
            payload={
                "source_tool": source_tool,
                "trust_score": trust_score,
                "conversation_id": str(conversation_id) if conversation_id else None,
            },
        )
        self._session.add(audit)
        await self._session.commit()

        log.info(
            "memory.long.inserted",
            memory_id=str(memory.id),
            user_id=str(user_id),
            source_tool=source_tool,
        )
        return memory

    async def find_similar(
        self,
        *,
        user_id: uuid.UUID,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[MemoryLongTerm]:
        """Return up to top_k memories closest to query_embedding by cosine distance.

        Results are scoped to user_id — cross-user retrieval is impossible here
        (CLAUDE.md §6 Memory hygiene).
        """
        vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"
        result = await self._session.execute(
            text(
                "SELECT id FROM memory_long_term "
                "WHERE user_id = :user_id "
                "  AND embedding IS NOT NULL "
                "ORDER BY embedding <=> CAST(:vec AS vector) "
                "LIMIT :top_k"
            ),
            {"user_id": str(user_id), "vec": vec_str, "top_k": top_k},
        )
        row_ids = [uuid.UUID(row[0]) for row in result.fetchall()]

        if not row_ids:
            return []

        # Fetch full ORM objects (keeps service layer clean — no raw dicts).
        memories: list[MemoryLongTerm] = []
        for row_id in row_ids:
            obj = await self._session.get(MemoryLongTerm, row_id)
            if obj is not None:
                memories.append(obj)
        return memories
