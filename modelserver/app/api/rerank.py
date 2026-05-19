"""POST /v1/rerank — cross-encoder scoring for hybrid retrieval reranking."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1", tags=["rerank"])


class RerankRequest(BaseModel):
    query: str = Field(min_length=1, max_length=5_000)
    candidates: list[str] = Field(min_length=1, max_length=50)


class RerankResponse(BaseModel):
    scores: list[float] = Field(
        description="One relevance score per candidate, same order as input."
    )


@router.post("/rerank", response_model=RerankResponse)
async def rerank(request: Request, body: RerankRequest) -> RerankResponse:
    """Score (query, candidate) pairs with the cross-encoder reranker."""
    service = request.app.state.reranker
    scores = await service.score(body.query, body.candidates)
    return RerankResponse(scores=scores)
