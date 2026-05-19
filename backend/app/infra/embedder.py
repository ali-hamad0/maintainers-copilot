"""Gemini embedding adapter (gemini-embedding-001, 768-dim).

Batches up to 100 texts per REST call (Gemini hard limit).
A 0.1 s pause is inserted between batches to stay inside rate limits.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

log = structlog.get_logger(__name__)

_BATCH_EMBED_URL = (
    "https://generativelanguage.googleapis.com/v1beta"
    "/models/gemini-embedding-001:batchEmbedContents"
)
_BATCH_SIZE = 100  # Gemini batchEmbedContents hard limit


class Embedder:
    """Async Gemini embedding client; keep alive for service lifetime."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        log.info("embedder.ready", model="gemini-embedding-001", dim=768)

    async def close(self) -> None:
        await self._client.aclose()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return one 768-d vector per text, in the same order."""
        if not texts:
            return []

        result: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            vectors = await self._call_api(batch)
            result.extend(vectors)
            if i + _BATCH_SIZE < len(texts):
                await asyncio.sleep(0.1)

        log.info("embedder.done", total=len(texts))
        return result

    async def _call_api(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "requests": [
                {
                    "model": "models/gemini-embedding-001",
                    "content": {"parts": [{"text": t}]},
                    "outputDimensionality": 768,
                }
                for t in texts
            ]
        }
        resp = await self._client.post(
            _BATCH_EMBED_URL,
            params={"key": self._api_key},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return [item["values"] for item in data["embeddings"]]
