"""Tool: summarise_issue — calls modelserver POST /v1/summarise."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
import structlog
from pydantic import BaseModel, Field

from app.domain.tool_result import ToolError
from app.tools import ToolContext, clean_schema_for_gemini

log = structlog.get_logger(__name__)


class SummariseArgs(BaseModel):
    issue_title: str = Field(description="Issue title.", min_length=1, max_length=500)
    issue_body: str = Field(description="Issue body text.", default="", max_length=20_000)
    comments: list[str] = Field(
        description="Up to 10 comment texts from the issue thread.",
        default_factory=list,
        max_length=10,
    )


class SummariseResult(BaseModel):
    summary: str = Field(description="2-3 sentence summary of the issue thread.")
    model: str = Field(description="Model used: gemini-2.5-flash or fallback.")
    fallback: bool = Field(description="True if LLM was not called and a plain excerpt was used.")


class SummariseTool:
    name: ClassVar[str] = "summarise_issue"
    description: ClassVar[str] = (
        "Summarise a GitHub issue thread (title, body, and optional comments) "
        "into 2-3 sentences. Useful for quickly understanding a long issue."
    )
    args_schema: ClassVar[type[BaseModel]] = SummariseArgs

    def __init__(self, http: httpx.AsyncClient, modelserver_url: str) -> None:
        self._http = http
        self._url = f"{modelserver_url}/v1/summarise"

    def declaration(self) -> dict[str, Any]:
        schema = SummariseArgs.model_json_schema()
        return {
            "name": self.name,
            "description": self.description,
            "parameters": clean_schema_for_gemini(schema),
        }

    async def run(
        self, args: dict[str, Any], ctx: ToolContext  # noqa: ARG002
    ) -> SummariseResult | ToolError:
        try:
            parsed = SummariseArgs.model_validate(args)
        except Exception as exc:
            return ToolError(error=f"Invalid args: {exc}", retryable=False)

        try:
            resp = await self._http.post(
                self._url,
                json={
                    "title": parsed.issue_title,
                    "body": parsed.issue_body,
                    "comments": parsed.comments,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.TimeoutException:
            log.warning("tool.summarise.timeout")
            return ToolError(error="Summariser timed out.", retryable=True)
        except httpx.HTTPStatusError as exc:
            log.warning("tool.summarise.http_error", status=exc.response.status_code)
            return ToolError(
                error=f"Summariser returned {exc.response.status_code}.", retryable=True
            )
        except httpx.HTTPError as exc:
            log.warning("tool.summarise.network_error", error=str(exc))
            return ToolError(error="Summariser unreachable.", retryable=True)

        data = resp.json()
        log.info("tool.summarise.done", fallback=data.get("fallback"))
        return SummariseResult(
            summary=data["summary"],
            model=data["model"],
            fallback=data["fallback"],
        )
