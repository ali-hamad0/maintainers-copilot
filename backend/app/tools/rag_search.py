"""Tool: rag_search — hybrid retrieval via the injected RetrievalService.

The tool receives a pre-built RetrievalService instance (created in lifespan)
so it does not import any repo or infra code directly.  The AsyncSession is
passed per-call through ToolContext so it participates in the request transaction.
"""

from __future__ import annotations

from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, Field

from app.domain.tool_result import ToolError
from app.services.retrieval import RetrievalService
from app.tools import ToolContext, clean_schema_for_gemini

log = structlog.get_logger(__name__)


class RagSearchArgs(BaseModel):
    query: str = Field(
        description="Natural-language question to search for in project docs and resolved issues.",
        min_length=1,
        max_length=1_000,
    )
    doc_type: str | None = Field(
        default=None,
        description="Optional filter: 'issue' or 'docs'. Omit to search both.",
    )
    top_k: int = Field(
        default=5,
        description="Maximum number of results to return (1-10).",
        ge=1,
        le=10,
    )


class RagChunk(BaseModel):
    content: str = Field(description="Chunk content.")
    doc_id: str = Field(description="Source document identifier.")
    doc_type: str = Field(description="Document type: issue or docs.")
    score: float = Field(description="Cross-encoder relevance score.")


class RagSearchResult(BaseModel):
    chunks: list[RagChunk] = Field(description="Retrieved chunks ordered by relevance.")
    found: bool = Field(description="True if any results were returned.")


class RagSearchTool:
    name: ClassVar[str] = "rag_search"
    description: ClassVar[str] = (
        "Search the project's documentation and resolved GitHub issues using hybrid "
        "retrieval (BM25 + dense + rerank). Always call this before answering technical "
        "questions that may have project-specific context."
    )
    args_schema: ClassVar[type[BaseModel]] = RagSearchArgs

    def __init__(self, retrieval: RetrievalService) -> None:
        self._retrieval = retrieval

    def declaration(self) -> dict[str, Any]:
        schema = RagSearchArgs.model_json_schema()
        return {
            "name": self.name,
            "description": self.description,
            "parameters": clean_schema_for_gemini(schema),
        }

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> RagSearchResult | ToolError:
        try:
            parsed = RagSearchArgs.model_validate(args)
        except Exception as exc:
            return ToolError(error=f"Invalid args: {exc}", retryable=False)

        filters: dict[str, Any] = {}
        if parsed.doc_type:
            filters["doc_type"] = parsed.doc_type

        try:
            chunks = await self._retrieval.retrieve(
                query=parsed.query,
                filters=filters,
                k=parsed.top_k,
                session=ctx.session,
            )
        except Exception as exc:
            log.warning("tool.rag_search.error", error=str(exc))
            return ToolError(error=f"Retrieval failed: {exc}", retryable=True)

        log.info("tool.rag_search.done", result_count=len(chunks))
        return RagSearchResult(
            chunks=[
                RagChunk(
                    content=c.child.content,
                    doc_id=c.child.doc_id,
                    doc_type=c.child.doc_type,
                    score=c.score,
                )
                for c in chunks
            ],
            found=len(chunks) > 0,
        )
