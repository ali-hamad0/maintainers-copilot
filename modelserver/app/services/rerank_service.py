"""Cross-encoder reranking service (ms-marco-MiniLM-L-6-v2).

Wraps sentence-transformers CrossEncoder.  CPU inference runs in asyncio.to_thread
so the event loop stays unblocked.  One instance per process lifetime — loaded
in modelserver lifespan, not per-request.
"""

from __future__ import annotations

import asyncio

import numpy as np
import structlog
from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]

log = structlog.get_logger(__name__)


class RerankService:
    """Score (query, candidate) pairs with a cross-encoder model."""

    def __init__(self, model_name: str) -> None:
        self._model: CrossEncoder = CrossEncoder(model_name)
        log.info("rerank_service.ready", model=model_name)

    async def score(self, query: str, candidates: list[str]) -> list[float]:
        """Return one relevance score per candidate (higher = more relevant)."""
        if not candidates:
            return []
        pairs = [(query, c) for c in candidates]
        scores_array: np.ndarray = await asyncio.to_thread(self._model.predict, pairs)
        scores: list[float] = scores_array.tolist()
        log.debug("rerank_service.scored", candidates=len(candidates))
        return scores
