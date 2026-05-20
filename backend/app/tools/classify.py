"""Tool: classify_issue — calls modelserver POST /v1/classify."""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
import structlog
from pydantic import BaseModel, Field

from app.domain.tool_result import ToolError
from app.tools import ToolContext, clean_schema_for_gemini

log = structlog.get_logger(__name__)


class ClassifyArgs(BaseModel):
    issue_title: str = Field(description="Title of the GitHub issue.", min_length=1, max_length=500)
    issue_body: str = Field(
        description="Body text of the GitHub issue.", default="", max_length=10_000
    )


class ClassifyResult(BaseModel):
    label: str = Field(description="Predicted class: bug | feature | docs | question.")
    confidence: float = Field(description="Confidence of the top prediction (0-1).")
    all_scores: dict[str, float] = Field(description="Softmax probabilities for all classes.")


class ClassifyTool:
    name: ClassVar[str] = "classify_issue"
    description: ClassVar[str] = (
        "Classify a GitHub issue into bug, feature, docs, or question. "
        "Returns the predicted label and a confidence score."
    )
    args_schema: ClassVar[type[BaseModel]] = ClassifyArgs

    def __init__(self, http: httpx.AsyncClient, modelserver_url: str) -> None:
        self._http = http
        self._url = f"{modelserver_url}/v1/classify"

    def declaration(self) -> dict[str, Any]:
        schema = ClassifyArgs.model_json_schema()
        return {
            "name": self.name,
            "description": self.description,
            "parameters": clean_schema_for_gemini(schema),
        }

    async def run(
        self, args: dict[str, Any], ctx: ToolContext  # noqa: ARG002
    ) -> ClassifyResult | ToolError:
        try:
            parsed = ClassifyArgs.model_validate(args)
        except Exception as exc:
            return ToolError(error=f"Invalid args: {exc}", retryable=False)

        text = f"{parsed.issue_title}\n\n{parsed.issue_body}"
        try:
            resp = await self._http.post(
                self._url,
                json={"text": text[:10_000]},
                timeout=10.0,
            )
            resp.raise_for_status()
        except httpx.TimeoutException:
            log.warning("tool.classify.timeout")
            return ToolError(error="Classifier timed out.", retryable=True)
        except httpx.HTTPStatusError as exc:
            log.warning("tool.classify.http_error", status=exc.response.status_code)
            return ToolError(
                error=f"Classifier returned {exc.response.status_code}.", retryable=True
            )
        except httpx.HTTPError as exc:
            log.warning("tool.classify.network_error", error=str(exc))
            return ToolError(error="Classifier unreachable.", retryable=True)

        data = resp.json()
        log.info("tool.classify.done", label=data.get("label"))
        return ClassifyResult(
            label=data["label"],
            confidence=data["score"],
            all_scores=data["scores"],
        )
