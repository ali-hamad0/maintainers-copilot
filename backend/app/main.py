from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import bootcheck
from app.api.auth_router import router as auth_router
from app.api.chat_router import router as chat_router
from app.api.errors import register_exception_handlers
from app.api.memories_router import router as memories_router
from app.api.middleware import CORSFromDBMiddleware, RequestIDMiddleware
from app.api.widget_router import router as widget_router
from app.config import get_settings
from app.infra.embedder import Embedder
from app.infra.logging_setup import configure_logging
from app.infra.reranker import Reranker
from app.infra.tracing import configure_tracing, emit_startup_span
from app.infra.vault import load_secrets
from app.repositories.chunk_repo import ChunkRepo
from app.services.agent import AgentService
from app.services.retrieval import RetrievalService
from app.tools.classify import ClassifyTool
from app.tools.extract_entities import ExtractEntitiesTool
from app.tools.rag_search import RagSearchTool
from app.tools.summarise import SummariseTool
from app.tools.write_memory import WriteMemoryTool

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

    # 7. Embedder (Gemini REST; shared by ingest + retrieval).
    embedder = Embedder(api_key=secrets.gemini_api_key)
    app.state.embedder = embedder
    log.info("embedder.attached", model=settings.embedding_model)

    # 8. Reranker HTTP client → modelserver /v1/rerank.
    reranker = Reranker(modelserver_base_url=settings.modelserver_base_url)
    app.state.reranker = reranker

    # 9. Retrieval service — single entry point for all RAG retrieval.
    retrieval_svc = RetrievalService(
        embedder=embedder,
        reranker=reranker,
        chunk_repo=ChunkRepo(),
        gemini_api_key=secrets.gemini_api_key,
        rrf_k=settings.hybrid_rrf_k,
    )
    app.state.retrieval = retrieval_svc
    log.info("retrieval.service_ready", rrf_k=settings.hybrid_rrf_k)

    # 10. Tool-calling agent — ONE registry, ONE loop (Phase 13).
    modelserver_url = settings.modelserver_base_url
    shared_http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    agent = AgentService(
        gemini_api_key=secrets.gemini_api_key,
        model=settings.agent_model,
        max_iterations=settings.agent_max_iterations,
        classify_tool=ClassifyTool(shared_http, modelserver_url),
        extract_entities_tool=ExtractEntitiesTool(shared_http, modelserver_url),
        summarise_tool=SummariseTool(shared_http, modelserver_url),
        rag_search_tool=RagSearchTool(retrieval_svc),
        write_memory_tool=WriteMemoryTool(embedder),
        embedder=embedder,
    )
    app.state.agent = agent
    log.info("agent.ready", model=settings.agent_model, max_iter=settings.agent_max_iterations)

    log.info("api.started")
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    log.info("api.shutting_down")
    await agent.close()
    await shared_http.aclose()
    await retrieval_svc.close()
    await reranker.close()
    await embedder.close()
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

    # Middleware runs outermost-first.
    # RequestIDMiddleware must be innermost so request_id is bound first.
    # CORSFromDBMiddleware wraps everything so CORS headers appear on all responses.
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(CORSFromDBMiddleware)

    # Register the unified exception handler (CLAUDE.md §5 Errors).
    register_exception_handlers(app)

    # Auth + Users routes (Phase 12).
    app.include_router(auth_router)

    # Chat endpoint (Phase 13).
    app.include_router(chat_router)

    # Widget config + embed endpoint (Phase 14).
    app.include_router(widget_router)

    # Long-term memory inspector (Phase 14).
    app.include_router(memories_router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
