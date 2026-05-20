"""HTTP middleware for the API layer."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import structlog
from cachetools import TTLCache
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

log = structlog.get_logger(__name__)

# Cache the full set of allowed origins across all active widget configs.
# TTL=30s: a new widget config becomes active within 30 seconds of creation.
_origins_cache: TTLCache[str, set[str]] = TTLCache(maxsize=1, ttl=30)
_cache_lock = asyncio.Lock()

_CORS_METHODS = "GET, POST, PATCH, DELETE, OPTIONS"
_CORS_HEADERS = "Authorization, Content-Type, X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a UUID request_id to every request.

    Sets request.state.request_id and binds it to structlog contextvars so
    every log line emitted during the request carries the same ID.
    Cleared in finally so it does not bleed into the next request on the
    same async task.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()


class CORSFromDBMiddleware(BaseHTTPMiddleware):
    """CORS middleware that reads allowed origins from the widget_config DB table.

    On every request carrying an Origin header, the origin is validated against
    the union of all active widget configs' allowed_origins fields.  Results are
    cached for 30 seconds (TTL) to avoid per-request DB queries.

    This replaces a hardcoded env-var allowlist (CLAUDE.md §6 Security).
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        origin = request.headers.get("origin")

        if not origin:
            return await call_next(request)

        is_allowed = await self._is_allowed(request.app, origin)

        # Preflight — respond immediately without touching downstream routes.
        if request.method == "OPTIONS":
            if is_allowed:
                return Response(
                    status_code=200,
                    headers={
                        "Access-Control-Allow-Origin": origin,
                        "Access-Control-Allow-Methods": _CORS_METHODS,
                        "Access-Control-Allow-Headers": _CORS_HEADERS,
                        "Access-Control-Max-Age": "3600",
                        "Vary": "Origin",
                    },
                )
            log.warning("cors.preflight_blocked", origin=origin)
            return Response(status_code=403)

        response = await call_next(request)

        if is_allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"

        return response

    @staticmethod
    async def _is_allowed(app: Any, origin: str) -> bool:
        """Check origin against the TTL-cached set of allowed origins."""
        # Before DB is ready (startup), allow nothing.
        engine = getattr(getattr(app, "state", None), "db_engine", None)
        if engine is None:
            return False

        async with _cache_lock:
            cached = _origins_cache.get("all")
            if cached is not None:
                return origin in cached

            # Cache miss — query DB.
            try:
                async with AsyncSession(engine) as session:
                    from app.services.widget_service import WidgetService

                    all_origins = await WidgetService.get_all_allowed_origins(session)
            except Exception as exc:
                log.warning("cors.db_query_failed", error=str(exc))
                return False

            _origins_cache["all"] = all_origins
            log.debug(
                "cors.cache_refreshed",
                count=len(all_origins),
                ts=int(time.time()),
            )
            return origin in all_origins
