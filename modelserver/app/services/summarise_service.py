"""Summarise service: calls Gemini 2.5 Flash to summarise a GitHub issue thread.

Strategy: LLM-driven (see DECISIONS.md §D-P6-01).
- Prompt loaded from prompts/summarise_issue.txt (one prompt per file rule).
- httpx.AsyncClient is kept alive for the lifetime of the service instance.
- Timeout: 30 s on the Gemini REST call.
- Fallback: if the API key is absent or the call fails, return the first 350
  characters of the issue text so the caller always gets *something*.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog

log = structlog.get_logger(__name__)

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta" "/models/gemini-2.5-flash:generateContent"
)
_MAX_SUMMARY_TOKENS = 200
_FALLBACK_CHARS = 350

# Prompt file path relative to this file: ../../prompts/summarise_issue.txt
# i.e. modelserver/app/services/ -> modelserver/app/ -> modelserver/ -> prompts/
_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "summarise_issue.txt"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SummaryResult:
    summary: str
    model: str
    fallback: bool  # True when the LLM was not called and a truncation was returned


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class SummariseService:
    """LLM-driven issue summariser using Gemini 2.5 Flash."""

    def __init__(self, gemini_api_key: str) -> None:
        self._api_key = gemini_api_key
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")
        has_key = bool(gemini_api_key)
        log.info("summarise_service.ready", has_api_key=has_key)

    async def close(self) -> None:
        await self._client.aclose()

    def _fallback(self, title: str, body: str) -> SummaryResult:
        combined = f"{title}\n\n{body}".strip()
        truncated = combined[:_FALLBACK_CHARS]
        if len(combined) > _FALLBACK_CHARS:
            truncated += "..."
        return SummaryResult(summary=truncated, model="fallback", fallback=True)

    async def summarise(self, title: str, body: str) -> SummaryResult:
        """Return a 2-3 sentence summary of the issue.

        Falls back to a truncated excerpt if the API key is missing or the
        Gemini call fails.
        """
        if not self._api_key:
            log.warning("summarise_service.no_api_key")
            return self._fallback(title, body)

        prompt = self._prompt_template.format(title=title, body=body[:4_000])

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": _MAX_SUMMARY_TOKENS,
                "temperature": 0.1,
            },
        }

        try:
            resp = await self._client.post(
                _GEMINI_URL,
                params={"key": self._api_key},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            summary_text: str = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            log.info("summarise_service.ok", chars=len(summary_text))
            return SummaryResult(
                summary=summary_text,
                model="gemini-2.5-flash",
                fallback=False,
            )
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            log.warning("summarise_service.error", error=str(exc))
            return self._fallback(title, body)

    async def summarise_thread(self, title: str, body: str, comments: list[str]) -> SummaryResult:
        """Summarise a full issue thread (body + top comments).

        Runs in a thread because the string formatting may be slow for long
        threads; the actual Gemini call is already async.
        """
        thread_text = await asyncio.to_thread("\n\n---\n\n".join, [body, *comments[:10]])
        return await self.summarise(title, thread_text)
