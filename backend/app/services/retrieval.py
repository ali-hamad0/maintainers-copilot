"""RAG retrieval service — single entry point for all chunk retrieval.

Pipeline (in order):
  1. HyDE: generate a hypothetical issue document and embed it.
  2. Sparse search: BM25 via Postgres ts_rank (top-20 candidates).
  3. Dense search:  pgvector cosine on the HyDE embedding (top-20 candidates).
  4. RRF fusion:    merge the two ranked lists with Reciprocal Rank Fusion (k=60).
  5. Rerank:        cross-encoder scores the fused top-20; selects top-k.
  6. Parent fetch:  expand each matched child to its parent chunk for context.

NO retrieval logic lives outside this module.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.chunk import ChunkRecord, RetrievedChunk
from app.infra.embedder import Embedder
from app.infra.reranker import Reranker
from app.repositories.chunk_repo import ChunkRepo

log = structlog.get_logger(__name__)

_CANDIDATES = 20  # retrieved from each retriever before rerank
_GEMINI_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta" "/models/gemini-2.5-flash:generateContent"
)


def _load_hyde_prompt() -> str:
    path = Path(__file__).resolve().parents[2] / "prompts" / "hyde_expansion.md"
    with open(path) as fh:
        return fh.read()


def _rrf_fuse(
    sparse: list[ChunkRecord],
    dense: list[ChunkRecord],
    rrf_k: int,
) -> list[tuple[ChunkRecord, float]]:
    """Reciprocal Rank Fusion (Cormack, Clarke, Buettcher 2009).

    score(doc) = Σ_i  1 / (rrf_k + rank_i + 1)   [0-indexed rank]

    rrf_k=60 is the standard constant; it balances high-rank vs low-rank
    contributions.  Documents in only one list still get a score.
    """
    scores: dict[str, float] = {}
    id_to_chunk: dict[str, ChunkRecord] = {}

    for rank, chunk in enumerate(sparse):
        cid = str(chunk.id)
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
        id_to_chunk[cid] = chunk

    for rank, chunk in enumerate(dense):
        cid = str(chunk.id)
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
        id_to_chunk[cid] = chunk

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [(id_to_chunk[cid], score) for cid, score in ranked]


class RetrievalService:
    """Orchestrates the full hybrid + rerank retrieval pipeline.

    One instance per process; created in lifespan and closed on shutdown.
    The AsyncSession is passed per-call (one DB session per request).
    """

    def __init__(
        self,
        embedder: Embedder,
        reranker: Reranker,
        chunk_repo: ChunkRepo,
        gemini_api_key: str,
        rrf_k: int = 60,
    ) -> None:
        self._embedder = embedder
        self._reranker = reranker
        self._repo = chunk_repo
        self._gemini_api_key = gemini_api_key
        self._rrf_k = rrf_k
        self._hyde_prompt_template = _load_hyde_prompt()
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def close(self) -> None:
        await self._http.aclose()

    async def _hyde_expand(self, query: str) -> str:
        """Generate a hypothetical GitHub issue for the query (HyDE technique).

        Embedding the hypothesis instead of the raw query closes the vocabulary
        gap between short natural-language questions and long technical issue
        threads.  Falls back to the original query on any API error so retrieval
        continues in degraded mode rather than failing.
        """
        prompt = self._hyde_prompt_template.replace("{query}", query)
        payload: dict[str, object] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 200, "temperature": 0.3},
        }
        try:
            resp = await self._http.post(
                _GEMINI_GENERATE_URL,
                params={"key": self._gemini_api_key},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            hypothesis: str = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            log.debug(
                "retrieval.hyde_expanded",
                query_len=len(query),
                hyp_len=len(hypothesis),
            )
            return hypothesis
        except (httpx.HTTPError, KeyError, IndexError):
            log.warning("retrieval.hyde_failed_fallback", query=query[:80])
            return query

    async def retrieve(
        self,
        query: str,
        filters: dict[str, Any],
        k: int = 5,
        *,
        session: AsyncSession,
    ) -> list[RetrievedChunk]:
        """Run the full retrieval pipeline and return top-k ranked chunks.

        Args:
            query:   Natural-language question from the maintainer.
            filters: Metadata constraints (doc_type, closed_at_gte, etc.).
            k:       Maximum number of results to return.
            session: DB session for this request (passed as keyword-only arg).

        Returns:
            List of RetrievedChunk ordered by cross-encoder score descending.
        """

        # 1. HyDE: embed a plausible hypothetical answer document, not the query
        hyde_text = await self._hyde_expand(query)
        query_vec = (await self._embedder.embed_batch([hyde_text]))[0]

        # 2. Sparse (BM25) + dense (cosine) in parallel to reduce latency
        sparse_task = self._repo.sparse_search(session, query, filters, _CANDIDATES)
        dense_task = self._repo.dense_search(session, query_vec, filters, _CANDIDATES)
        sparse_results, dense_results = await asyncio.gather(sparse_task, dense_task)

        log.info(
            "retrieval.candidates",
            sparse=len(sparse_results),
            dense=len(dense_results),
        )

        if not sparse_results and not dense_results:
            log.warning("retrieval.empty_result", query=query[:80])
            return []

        # 3. RRF fusion: merge ranked lists, take top-20 as rerank candidates
        fused = _rrf_fuse(sparse_results, dense_results, self._rrf_k)[:_CANDIDATES]
        candidates = [chunk for chunk, _ in fused]

        # 4. Cross-encoder rerank → select top-k
        scores = await self._reranker.score_batch(query, [c.content for c in candidates])
        ranked_pairs = sorted(
            zip(candidates, scores, strict=True), key=lambda x: x[1], reverse=True
        )
        top_k = ranked_pairs[:k]

        # 5. Fetch parent chunks (deduped) for full-context window expansion
        parent_ids = list({chunk.parent_id for chunk, _ in top_k if chunk.parent_id is not None})
        parents = await self._repo.fetch_parents(session, parent_ids)

        log.info(
            "retrieval.complete",
            query_len=len(query),
            top_k=len(top_k),
            filters=filters,
        )

        return [
            RetrievedChunk(
                child=chunk,
                parent=parents.get(chunk.parent_id) if chunk.parent_id is not None else None,
                score=score,
            )
            for chunk, score in top_k
        ]
