"""POST /v1/summarise — LLM-driven issue thread summarisation."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.services.summarise_service import SummaryResult

router = APIRouter(prefix="/v1", tags=["summarise"])


class SummariseRequest(BaseModel):
    title: str = Field(
        description="Issue title.",
        min_length=1,
        max_length=500,
    )
    body: str = Field(
        description="Issue body text.",
        default="",
        max_length=20_000,
    )
    comments: list[str] = Field(
        description="Up to 10 comment texts included in the thread summary.",
        default_factory=list,
        max_length=10,
    )


class SummariseResponse(BaseModel):
    summary: str = Field(description="2-3 sentence summary of the issue thread.")
    model: str = Field(
        description="Model used: 'gemini-2.5-flash' or 'fallback' (truncated excerpt)."
    )
    fallback: bool = Field(
        description="True when the LLM was not called and a plain excerpt was returned."
    )


def _to_response(result: SummaryResult) -> SummariseResponse:
    return SummariseResponse(
        summary=result.summary,
        model=result.model,
        fallback=result.fallback,
    )


@router.post("/summarise", response_model=SummariseResponse)
async def summarise(request: Request, body: SummariseRequest) -> SummariseResponse:
    """Summarise a GitHub issue thread into 2-3 sentences."""
    service = request.app.state.summariser
    if body.comments:
        result: SummaryResult = await service.summarise_thread(body.title, body.body, body.comments)
    else:
        result = await service.summarise(body.title, body.body)
    return _to_response(result)
