"""HTTP client for the modelserver /v1/rerank endpoint.

The cross-encoder model (ms-marco-MiniLM-L-6-v2) lives in the modelserver
container alongside the classifier and NER model.  This adapter calls it
over HTTP — no PyTorch in the API container.
"""

from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger(__name__)


class Reranker:
    """HTTP adapter for the modelserver rerank endpoint.

    One instance per process; created in lifespan and closed on shutdown.
    """

    def __init__(self, modelserver_base_url: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=modelserver_base_url,
            timeout=httpx.Timeout(30.0),
        )
        log.info("reranker.client_ready", base_url=modelserver_base_url)

    async def close(self) -> None:
        await self._client.aclose()

    async def score_batch(self, query: str, candidates: list[str]) -> list[float]:
        """Return one cross-encoder score per candidate; higher = more relevant."""
        if not candidates:
            return []
        resp = await self._client.post(
            "/v1/rerank",
            json={"query": query, "candidates": candidates},
        )
        resp.raise_for_status()
        data: dict[str, list[float]] = resp.json()
        scores: list[float] = data["scores"]
        log.debug("reranker.scored", candidates=len(candidates))
        return scores
