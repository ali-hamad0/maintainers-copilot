from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import bootcheck
from app.config import get_settings
from app.infra.logging_setup import configure_logging
from app.infra.tracing import configure_tracing, emit_startup_span
from app.infra.vault import load_secrets

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    # 1. Logging must come first so every subsequent log line is structured.
    configure_logging(settings.log_level, settings.log_format)
    log.info("api.starting", environment=settings.environment)

    # 2. Startup assertions — refuse to boot on any failure (CLAUDE.md §4).
    await bootcheck.run_all(
        settings.vault_addr,
        settings.vault_root_token,
        settings.modelserver_base_url,
        settings.classifier_weights_sha256,
    )

    # 3. Load secrets from Vault.
    secrets = await load_secrets(settings.vault_addr, settings.vault_root_token)
    app.state.secrets = secrets

    # 4. Wire tracing before the first LLM/tool call can happen.
    configure_tracing(
        api_key=secrets.langsmith_api_key,
        project=settings.langsmith_project,
        enabled=settings.tracing_backend == "langsmith",
    )
    # Emit one real startup span so the tracing UI shows a live trace.
    await emit_startup_span(settings.environment)

    # 5. Build the async DB engine (singleton for the process lifetime).
    db_url = (
        f"postgresql+asyncpg://{settings.db_user}:{secrets.db_password}"
        f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
    )
    engine: AsyncEngine = create_async_engine(
        db_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=settings.debug,
    )
    app.state.db_engine = engine
    app.state.db_url = db_url
    log.info("db.engine_created", host=settings.db_host, db=settings.db_name)

    # 6. Redis connection pool.
    redis_pool = aioredis.from_url(settings.redis_url, decode_responses=True)
    app.state.redis = redis_pool
    log.info("redis.connected", url=settings.redis_url)

    log.info("api.started")
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    log.info("api.shutting_down")
    await redis_pool.aclose()  # type: ignore[attr-defined]
    await engine.dispose()
    log.info("api.shutdown_complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Maintainer's Copilot API",
        description="FastAPI backend for issue triage chatbot",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
