"""Long-term episodic memory backed by pgvector.

Memory type chosen: EPISODIC (D-07, finalised Phase 12).
Each row records what happened in a specific conversation: the maintainer's
triage decisions, expressed preferences, and key context exchanges.

Why episodic over semantic or procedural:
- Semantic would require abstracting/distilling facts from raw conversation
  turns — a separate NLP step that adds complexity without a clear accuracy
  gain at project scale.
- Procedural captures how-to knowledge (workflows).  The triage chatbot does
  not yet learn repeatable procedures from free-form chat.
- Episodic is the natural fit: every write is tied to one conversation_id +
  one actor, timestamps are first-class, and the provenance field records the
  full lineage needed for the audit trail (CLAUDE.md §6 Memory hygiene).

Trust score: 1.0 for primary source (maintainer input).  Agent re-ingested
content receives 0.7 to signal lower confidence (CLAUDE.md §6).

Redaction: payload is passed through app.infra.redaction.redact_dict() before
the DB write (call site #3 from D-P11-02).  The caller (Phase 13 tool) is
responsible for calling this service only after redaction, or this service
calls redact_dict internally — choice is made in Phase 13.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from app.repositories.memory_repo import MemoryRepo
from app.repositories.orm_models import MemoryLongTerm

log = structlog.get_logger(__name__)

# Trust scores (CLAUDE.md §6 Memory hygiene).
TRUST_PRIMARY = 1.0  # direct maintainer input
TRUST_AGENT_REINGESTED = 0.7  # agent output re-ingested as memory


class MemoryLongService:
    """Write and retrieve long-term episodic memories.

    Instantiated per-request from app.state.db_engine via Depends(get_db).
    The MemoryRepo handles all SQL; this service owns the business rules.
    """

    def __init__(self, repo: MemoryRepo) -> None:
        self._repo = repo

    async def write(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        content: str,
        embedding: list[float],
        source_tool: str,
        payload: dict[str, Any] | None = None,
        trust_score: float = TRUST_PRIMARY,
        request_id: str | None = None,
    ) -> MemoryLongTerm:
        """Persist one episodic memory and its audit log row.

        `write_memory` is the ONLY way long-term memory is written (CLAUDE.md §6).
        Auto-writes from the agent loop are forbidden.

        payload defaults to {"text": content} if not supplied.
        provenance is assembled here from the caller-provided metadata.
        """
        effective_payload: dict[str, Any] = payload or {"text": content}
        provenance: dict[str, Any] = {
            "actor_id": str(user_id),
            "source_tool": source_tool,
            "conversation_id": str(conversation_id) if conversation_id else None,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        memory = await self._repo.insert(
            user_id=user_id,
            conversation_id=conversation_id,
            content=content,
            embedding=embedding,
            source_tool=source_tool,
            trust_score=trust_score,
            payload=effective_payload,
            provenance=provenance,
            request_id=request_id,
        )
        log.info(
            "memory.long.written",
            memory_id=str(memory.id),
            user_id=str(user_id),
            trust_score=trust_score,
        )
        return memory

    async def query_similar(
        self,
        *,
        user_id: uuid.UUID,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[MemoryLongTerm]:
        """Return up to top_k episodic memories similar to the query.

        Scoped to user_id — cross-user retrieval never happens here.
        """
        return await self._repo.find_similar(
            user_id=user_id,
            query_embedding=query_embedding,
            top_k=top_k,
        )
