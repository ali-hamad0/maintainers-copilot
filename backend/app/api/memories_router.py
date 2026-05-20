"""GET /memories — return current user's long-term memories (for memory inspector UI)."""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import current_active_user, get_db
from app.repositories.orm_models import MemoryLongTerm, User

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/memories", tags=["memories"])


class MemoryRead(BaseModel):
    id: uuid.UUID
    content: str
    source_tool: str
    trust_score: float
    conversation_id: uuid.UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[MemoryRead])
async def list_memories(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(current_active_user),
) -> list[MemoryRead]:
    """Return all long-term memories for the authenticated user, newest first."""
    result = await session.execute(
        select(MemoryLongTerm)
        .where(MemoryLongTerm.user_id == user.id)
        .order_by(MemoryLongTerm.created_at.desc())
        .limit(100)
    )
    rows = list(result.scalars().all())
    log.info("memories.list", user_id=str(user.id), count=len(rows))
    return [MemoryRead.model_validate(r) for r in rows]
