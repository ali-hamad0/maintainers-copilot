"""Tool: extract_entities — calls modelserver POST /v1/ner."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
import structlog
from pydantic import BaseModel, Field

from app.domain.tool_result import ToolError
from app.tools import ToolContext, clean_schema_for_gemini

log = structlog.get_logger(__name__)


class ExtractEntitiesArgs(BaseModel):
    text: str = Field(
        description="Issue text to extract entities from.", min_length=1, max_length=20_000
    )


class Entity(BaseModel):
    text: str = Field(description="The entity surface form.")
    label: str = Field(
        description="Entity type: FILE_PATH, CODE_ENTITY, ERROR_TYPE, VERSION, or spaCy standard."
    )
    source: str = Field(description="Extraction method: regex or spacy.")


class ExtractEntitiesResult(BaseModel):
    entities: list[Entity] = Field(description="Extracted entities, deduplicated.")
    spacy_available: bool = Field(description="False if spaCy model was not loaded (regex only).")


class ExtractEntitiesTool:
    name: ClassVar[str] = "extract_entities"
    description: ClassVar[str] = (
        "Extract code entities, error types, version numbers, and file paths from issue text. "
        "Returns a deduplicated list of typed entities."
    )
    args_schema: ClassVar[type[BaseModel]] = ExtractEntitiesArgs

    def __init__(self, http: httpx.AsyncClient, modelserver_url: str) -> None:
        self._http = http
        self._url = f"{modelserver_url}/v1/ner"

    def declaration(self) -> dict[str, Any]:
        schema = ExtractEntitiesArgs.model_json_schema()
        return {
            "name": self.name,
            "description": self.description,
            "parameters": clean_schema_for_gemini(schema),
        }

    async def run(
        self, args: dict[str, Any], ctx: ToolContext  # noqa: ARG002
    ) -> ExtractEntitiesResult | ToolError:
        try:
            parsed = ExtractEntitiesArgs.model_validate(args)
        except Exception as exc:
            return ToolError(error=f"Invalid args: {exc}", retryable=False)

        try:
            resp = await self._http.post(
                self._url,
                json={"text": parsed.text},
                timeout=10.0,
            )
            resp.raise_for_status()
        except httpx.TimeoutException:
            log.warning("tool.ner.timeout")
            return ToolError(error="NER service timed out.", retryable=True)
        except httpx.HTTPStatusError as exc:
            log.warning("tool.ner.http_error", status=exc.response.status_code)
            return ToolError(
                error=f"NER service returned {exc.response.status_code}.", retryable=True
            )
        except httpx.HTTPError as exc:
            log.warning("tool.ner.network_error", error=str(exc))
            return ToolError(error="NER service unreachable.", retryable=True)

        data = resp.json()
        entities = [
            Entity(text=e["text"], label=e["label"], source=e["source"])
            for e in data.get("entities", [])
        ]
        log.info("tool.ner.done", entity_count=len(entities))
        return ExtractEntitiesResult(
            entities=entities,
            spacy_available=data.get("spacy_available", False),
        )
