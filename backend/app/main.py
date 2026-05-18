from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Maintainer's Copilot API",
        description="FastAPI backend for issue triage chatbot",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app


app = create_app()
