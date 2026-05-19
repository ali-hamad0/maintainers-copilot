"""POST /v1/ner — named entity extraction for GitHub issue text."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.services.ner_service import NEREntity, NERResult

router = APIRouter(prefix="/v1", tags=["ner"])


class NERRequest(BaseModel):
    text: str = Field(
        description="Issue text to extract entities from.",
        min_length=1,
        max_length=20_000,
    )


class NEREntityOut(BaseModel):
    text: str = Field(description="The entity surface form.")
    label: str = Field(
        description=(
            "Entity type: FILE_PATH | CODE_ENTITY | ERROR_TYPE | VERSION "
            "or a spaCy standard type (PERSON, ORG, PRODUCT, …)."
        )
    )
    source: str = Field(description="Extraction method: 'regex' or 'spacy'.")


class NERResponse(BaseModel):
    entities: list[NEREntityOut] = Field(description="All extracted entities, deduplicated.")
    spacy_available: bool = Field(
        description="False if the spaCy model was not loaded (regex-only mode)."
    )


def _to_entity_out(e: NEREntity) -> NEREntityOut:
    return NEREntityOut(text=e.text, label=e.label, source=e.source)


@router.post("/ner", response_model=NERResponse)
async def ner(request: Request, body: NERRequest) -> NERResponse:
    """Extract code-shaped and standard named entities from issue text."""
    service = request.app.state.ner
    result: NERResult = await service.extract(body.text)
    return NERResponse(
        entities=[_to_entity_out(e) for e in result.entities],
        spacy_available=service._nlp is not None,
    )
