"""POST /v1/classify — synchronous issue classification endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.services.classifier_service import ClassifyResult

router = APIRouter(prefix="/v1", tags=["classify"])

_VALID_LABELS = frozenset({"bug", "feature", "docs", "question"})


class ClassifyRequest(BaseModel):
    text: str = Field(
        description="Issue text in 'TITLE\\n\\nBODY' format, truncated to 512 chars max.",
        min_length=1,
        max_length=10_000,
    )


class ClassifyResponse(BaseModel):
    label: str = Field(description="Predicted class: bug | feature | docs | question")
    score: float = Field(description="Confidence of the top prediction (0–1)")
    scores: dict[str, float] = Field(description="Softmax probabilities for all classes")


def _to_response(result: ClassifyResult) -> ClassifyResponse:
    return ClassifyResponse(label=result.label, score=result.score, scores=result.scores)


@router.post("/classify", response_model=ClassifyResponse)
async def classify(request: Request, body: ClassifyRequest) -> ClassifyResponse:
    """Classify a GitHub issue into bug / feature / docs / question."""
    classifier = request.app.state.classifier
    result = await classifier.classify(body.text)
    return _to_response(result)
