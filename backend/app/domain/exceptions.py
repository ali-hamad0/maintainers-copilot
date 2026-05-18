"""Custom exception hierarchy (CLAUDE.md §5 Errors).

ONE @app.exception_handler in api/ maps these to HTTP responses with
code + request_id.  Users NEVER see a stack trace.
"""


class AppError(Exception):
    """Base for all application-layer errors."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class PermissionDenied(AppError):
    status_code = 403
    code = "permission_denied"


class ToolFailure(AppError):
    """Returned (not raised) to the agent loop for retryable or terminal failures."""

    status_code = 500
    code = "tool_failure"

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
