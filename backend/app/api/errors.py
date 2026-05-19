"""Global exception handlers.

ONE registration point (register_exception_handlers) maps every AppError
subclass to a structured HTTP response envelope: {code, message, request_id}.
Users never see a stack trace.  Internal details go to structured logs only.

HTTP status contract:
  401 — no/expired/bad token      (handled by fastapi-users, not here)
  403 — authenticated, not allowed → PermissionDenied
  404 — resource missing           → NotFoundError
  422 — validation failure         → ValidationFailure / RequestValidationError
  500 — unexpected error           → AppError / unhandled Exception
"""

import uuid
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain.exceptions import AppError

log = structlog.get_logger(__name__)


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", None) or uuid.uuid4())


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        req_id = _request_id(request)
        if exc.status_code >= 500:
            log.error(
                "request.app_error",
                code=exc.code,
                request_id=req_id,
                exc_info=exc,
            )
            user_message = "An internal error occurred."
        else:
            log.warning(
                "request.app_error",
                code=exc.code,
                message=exc.message,
                request_id=req_id,
            )
            user_message = exc.message
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": user_message, "request_id": req_id},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        req_id = _request_id(request)
        log.warning("request.validation_error", request_id=req_id, errors=str(exc.errors()))
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "Request validation failed.",
                "request_id": req_id,
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Let Starlette's HTTPException handler deal with its own exceptions
        # (404 from routing, 405 method not allowed, etc.).
        # This handler only fires for truly unhandled Python exceptions.
        from starlette.exceptions import HTTPException as StarletteHTTPException

        if isinstance(exc, StarletteHTTPException):
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "code": "http_error",
                    "message": str(exc.detail),
                    "request_id": _request_id(request),
                },
            )
        req_id = _request_id(request)
        log.error(
            "request.unhandled_error",
            request_id=req_id,
            exc_info=exc,
        )
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "An unexpected error occurred.",
                "request_id": req_id,
            },
        )

    # Suppress unused-variable mypy warnings — the decorators register the handlers.
    _: Any = handle_app_error
    __: Any = handle_validation_error
    ___: Any = handle_unexpected_error
