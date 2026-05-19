"""ModelServer — FastAPI inference server for classifier, NER, and summariser."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.classify_router import router as classify_router
from app.api.ner import router as ner_router
from app.api.rerank import router as rerank_router
from app.api.summarise import router as summarise_router
from app.config import get_settings
from app.infra.weight_loader import download_and_verify
from app.services.classifier_service import ClassifierService
from app.services.ner_service import NERService
from app.services.rerank_service import RerankService
from app.services.summarise_service import SummariseService

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    log.info("modelserver.starting", environment=settings.environment)

    # ── Classifier ────────────────────────────────────────────────────────────
    # Download weights from MinIO and verify SHA-256 (CLAUDE.md §4 #2, #3).
    weights_path = download_and_verify(
        minio_endpoint=settings.minio_endpoint,
        minio_access_key=settings.minio_access_key,
        minio_secret_key=settings.minio_secret_key,
        minio_bucket=settings.minio_bucket,
        minio_key=settings.weights_minio_key,
        expected_sha256=settings.weights_sha256,
    )
    actual_sha256 = hashlib.sha256(weights_path.read_bytes()).hexdigest()

    log.info("modelserver.loading_classifier", weights=str(weights_path))
    classifier = ClassifierService(weights_path)
    app.state.classifier = classifier
    app.state.weights_sha256 = actual_sha256
    log.info("modelserver.classifier_ready", sha256=actual_sha256)

    # ── NER ───────────────────────────────────────────────────────────────────
    # spaCy en_core_web_sm is CPU-only (~12 MB).  If the model is not installed
    # the service degrades gracefully to regex-only extraction.
    nlp: object | None = None
    try:
        import spacy

        nlp = spacy.load("en_core_web_sm")
        log.info("modelserver.spacy_loaded")
    except OSError:
        log.warning(
            "modelserver.spacy_model_missing", hint="run: python -m spacy download en_core_web_sm"
        )
    except ImportError:
        log.warning("modelserver.spacy_not_installed")

    app.state.ner = NERService(nlp)

    # ── Summarise ─────────────────────────────────────────────────────────────
    summariser = SummariseService(settings.gemini_api_key)
    app.state.summariser = summariser

    # ── Reranker ──────────────────────────────────────────────────────────────
    # CrossEncoder is CPU-only (~22 MB weights); downloaded from HuggingFace on
    # first boot and cached locally.  Subsequent boots use the local cache.
    log.info("modelserver.loading_reranker", model=settings.reranker_model)
    reranker = RerankService(settings.reranker_model)
    app.state.reranker = reranker
    log.info("modelserver.reranker_ready", model=settings.reranker_model)

    log.info("modelserver.started")
    yield

    # ── Cleanup ───────────────────────────────────────────────────────────────
    await app.state.summariser.close()
    log.info("modelserver.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Maintainer's Copilot ModelServer",
        description="FastAPI inference server for NLP models",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(classify_router)
    app.include_router(ner_router)
    app.include_router(rerank_router)
    app.include_router(summarise_router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, object]:
        sha256 = getattr(app.state, "weights_sha256", None)
        spacy_ok = getattr(app.state, "ner", None) is not None
        return {
            "status": "ok",
            "model_loaded": sha256 is not None,
            "weights_sha256": sha256,
            "spacy_available": spacy_ok and getattr(app.state.ner, "_nlp", None) is not None,
        }

    return app


app = create_app()
