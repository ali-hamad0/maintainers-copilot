"""Tool layer — per-request context and shared schema utilities."""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ToolContext:
    """Per-request data injected into every tool call."""

    session: AsyncSession
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    request_id: str | None = field(default=None)


def clean_schema_for_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip Pydantic-specific keys Gemini rejects; keep JSON Schema essentials.

    Gemini function parameter schemas accept: type, description, properties,
    required, items, enum.  Keys like $defs, title, default, and anyOf cause
    400 errors from the Gemini REST API.
    """
    allowed = {"type", "description", "properties", "required", "items", "enum"}
    result = {k: v for k, v in schema.items() if k in allowed}
    if "properties" in result:
        result["properties"] = {
            k: clean_schema_for_gemini(v) for k, v in result["properties"].items()
        }
    if "items" in result:
        result["items"] = clean_schema_for_gemini(result["items"])
    return result
