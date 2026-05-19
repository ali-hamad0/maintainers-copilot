"""ModelServer — FastAPI inference server for classifier, NER, and summariser."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.classify_router import router as classify_router
from app.config import get_settings
from app.infra.weight_loader import download_and_verify
from app.services.classifier_service import ClassifierService

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    log.info("modelserver.starting", environment=settings.environment)

    # Download weights from MinIO and verify SHA-256 (CLAUDE.md §4 #2, #3).
    # Raises RuntimeError → process exits before accepting traffic.
    weights_path = download_and_verify(
        minio_endpoint=settings.minio_endpoint,
        minio_access_key=settings.minio_access_key,
        minio_secret_key=settings.minio_secret_key,
        minio_bucket=settings.minio_bucket,
        minio_key=settings.weights_minio_key,
        expected_sha256=settings.weights_sha256,
    )

    # Compute actual SHA-256 for the healthz response so the api bootcheck
    # can verify it without re-downloading.
    actual_sha256 = hashlib.sha256(weights_path.read_bytes()).hexdigest()

    log.info("modelserver.loading_classifier", weights=str(weights_path))
    classifier = ClassifierService(weights_path)
    app.state.classifier = classifier
    app.state.weights_sha256 = actual_sha256
    log.info("modelserver.classifier_ready", sha256=actual_sha256)

    log.info("modelserver.started")
    yield

    log.info("modelserver.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Maintainer's Copilot ModelServer",
        description="FastAPI inference server for NLP models",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(classify_router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, object]:
        sha256 = getattr(app.state, "weights_sha256", None)
        return {
            "status": "ok",
            "model_loaded": sha256 is not None,
            "weights_sha256": sha256,
        }

    return app


app = create_app()
