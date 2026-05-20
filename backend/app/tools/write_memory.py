"""Tool: write_memory — persist an episodic memory to pgvector.

This is call site #3 for redaction (see app/infra/redaction.py).
Content is redacted BEFORE the DB write so no secrets reach the memory store.

write_memory is the ONLY path by which long-term memory is written.
The agent loop never calls it automatically — it only fires when the LLM
emits the write_memory function call explicitly (CLAUDE.md §6 Tool safety).
"""

from __future__ import annotations

from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, Field

from app.domain.tool_result import ToolError
from app.infra.embedder import Embedder
from app.infra.redaction import redact
from app.repositories.memory_repo import MemoryRepo
from app.services.memory_long import TRUST_PRIMARY, MemoryLongService
from app.tools import ToolContext, clean_schema_for_gemini

log = structlog.get_logger(__name__)


class WriteMemoryArgs(BaseModel):
    content: str = Field(
        description=(
            "The fact or decision to remember. Should be a single, self-contained statement "
            "meaningful on its own in a future conversation."
        ),
        min_length=1,
        max_length=2_000,
    )


class WriteMemoryResult(BaseModel):
    memory_id: str = Field(description="UUID of the newly created memory row.")


class WriteMemoryTool:
    name: ClassVar[str] = "write_memory"
    description: ClassVar[str] = (
        "Persist an important fact or triage decision to long-term memory so it is "
        "available in future conversations. Call ONLY when the maintainer explicitly "
        "asks to remember something. Do NOT call after every message."
    )
    args_schema: ClassVar[type[BaseModel]] = WriteMemoryArgs

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder

    def declaration(self) -> dict[str, Any]:
        schema = WriteMemoryArgs.model_json_schema()
        return {
            "name": self.name,
            "description": self.description,
            "parameters": clean_schema_for_gemini(schema),
        }

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> WriteMemoryResult | ToolError:
        try:
            parsed = WriteMemoryArgs.model_validate(args)
        except Exception as exc:
            return ToolError(error=f"Invalid args: {exc}", retryable=False)

        # Redaction call site #3 — before the content leaves this boundary.
        clean_content = redact(parsed.content)

        try:
            vectors = await self._embedder.embed_batch([clean_content])
        except Exception as exc:
            log.warning("tool.write_memory.embed_failed", error=str(exc))
            return ToolError(error="Embedding failed; memory not saved.", retryable=True)

        embedding = vectors[0]
        repo = MemoryRepo(ctx.session)
        memory_svc = MemoryLongService(repo)

        try:
            memory = await memory_svc.write(
                user_id=ctx.user_id,
                conversation_id=ctx.conversation_id,
                content=clean_content,
                embedding=embedding,
                source_tool=self.name,
                trust_score=TRUST_PRIMARY,
                request_id=ctx.request_id,
            )
        except Exception as exc:
            log.warning("tool.write_memory.db_failed", error=str(exc))
            return ToolError(error="Memory write failed.", retryable=True)

        log.info("tool.write_memory.done", memory_id=str(memory.id))
        return WriteMemoryResult(memory_id=str(memory.id))
