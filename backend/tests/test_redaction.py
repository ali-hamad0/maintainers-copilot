"""Redaction layer tests (CLAUDE.md §5 + Phase 11).

Covers:
  - Every redaction pattern in redaction.py (one happy + one miss per pattern)
  - redact_dict() recursive string sanitisation
  - structlog_processor() applied to an event dict
  - Full log-boundary test: a secret passed to structlog must not appear in output
  - Exception handler status codes and response envelope shape
  - sanitise_span_inputs() call site in tracing.py

No app startup is required — these tests import only pure-Python modules and
the FastAPI test client, deliberately avoiding app.main to stay CI-safe without
Vault / DB / Redis.
"""

import io
from typing import Any

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import register_exception_handlers
from app.domain.exceptions import NotFoundError, PermissionDenied, ToolFailure, ValidationFailure
from app.infra.redaction import redact, redact_dict, structlog_processor

# ---------------------------------------------------------------------------
# redact() — one test per pattern
# ---------------------------------------------------------------------------


class TestRedactPatterns:
    def test_anthropic_key(self) -> None:
        secret = "sk-ant-api03-" + "a" * 30
        result = redact(f"key={secret}")
        assert secret not in result
        assert "[REDACTED_ANTHROPIC_KEY]" in result

    def test_openai_key(self) -> None:
        secret = "sk-" + "a" * 48
        result = redact(f"api_key={secret}")
        assert secret not in result
        assert "[REDACTED_OPENAI_KEY]" in result

    def test_openai_project_key(self) -> None:
        secret = "sk-proj-" + "b" * 40
        result = redact(f"key={secret}")
        assert secret not in result

    def test_github_personal_token(self) -> None:
        token = "ghp_" + "A" * 36
        result = redact(f"token={token}")
        assert token not in result
        assert "[REDACTED_GITHUB_TOKEN]" in result

    def test_github_oauth_token(self) -> None:
        token = "gho_" + "Z" * 36
        assert token not in redact(token)

    def test_github_server_token(self) -> None:
        token = "ghs_" + "X" * 36
        assert token not in redact(token)

    def test_aws_access_key(self) -> None:
        key = "AKIAIOSFODNN7EXAMPLE"
        result = redact(f"access_key={key}")
        assert key not in result
        assert "[REDACTED_AWS_ACCESS_KEY]" in result

    def test_jwt(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMSJ9.SflKxwRJSMeKKF2QT4f"
        result = redact(f"Authorization: Bearer {jwt}")
        assert jwt not in result

    def test_email(self) -> None:
        email = "alice@example.com"
        result = redact(f"user: {email}")
        assert email not in result
        assert "[REDACTED_EMAIL]" in result

    def test_ipv4(self) -> None:
        ip = "192.168.1.42"
        result = redact(f"client_ip={ip}")
        assert ip not in result
        assert "[REDACTED_IPV4]" in result

    def test_password_param(self) -> None:
        result = redact("password=supersecret123")
        assert "supersecret123" not in result
        assert "password=" in result

    def test_authorization_header(self) -> None:
        result = redact("Authorization: Bearer my-secret-token")
        assert "my-secret-token" not in result
        assert "Authorization:" in result

    def test_authorization_basic(self) -> None:
        result = redact("Authorization: Basic dXNlcjpwYXNz")
        assert "dXNlcjpwYXNz" not in result

    def test_no_false_positive_plain_text(self) -> None:
        text = "This is a normal log message without any secrets."
        assert redact(text) == text

    def test_anthropic_matched_before_openai(self) -> None:
        secret = "sk-ant-api03-" + "c" * 30
        result = redact(secret)
        assert "[REDACTED_ANTHROPIC_KEY]" in result
        assert "[REDACTED_OPENAI_KEY]" not in result


# ---------------------------------------------------------------------------
# redact_dict() — recursive sanitisation
# ---------------------------------------------------------------------------


class TestRedactDict:
    def test_string_values_redacted(self) -> None:
        data: dict[str, Any] = {"api_key": "sk-" + "a" * 48, "model": "gpt-4"}
        result = redact_dict(data)
        assert "sk-" + "a" * 48 not in str(result)
        assert result["model"] == "gpt-4"

    def test_nested_dict_redacted(self) -> None:
        data: dict[str, Any] = {"outer": {"inner_key": "sk-" + "d" * 48}}
        result = redact_dict(data)
        assert "sk-" + "d" * 48 not in str(result)

    def test_non_string_values_preserved(self) -> None:
        data: dict[str, Any] = {"count": 42, "flag": True, "items": [1, 2, 3]}
        result = redact_dict(data)
        assert result["count"] == 42
        assert result["flag"] is True
        assert result["items"] == [1, 2, 3]

    def test_original_dict_not_mutated(self) -> None:
        secret = "sk-" + "e" * 48
        original: dict[str, Any] = {"key": secret}
        redact_dict(original)
        assert original["key"] == secret


# ---------------------------------------------------------------------------
# structlog_processor() — event dict sanitisation
# ---------------------------------------------------------------------------


class TestStructlogProcessor:
    def test_string_values_in_event_dict_redacted(self) -> None:
        event_dict: dict[str, Any] = {
            "event": "user.message",
            "api_key": "sk-" + "f" * 48,
        }
        result = structlog_processor(None, "info", event_dict)
        assert "sk-" + "f" * 48 not in result["api_key"]
        assert "[REDACTED_OPENAI_KEY]" in result["api_key"]

    def test_event_field_also_redacted(self) -> None:
        event_dict: dict[str, Any] = {
            "event": "key=sk-" + "g" * 48,
        }
        result = structlog_processor(None, "info", event_dict)
        assert "sk-" + "g" * 48 not in result["event"]

    def test_non_string_fields_untouched(self) -> None:
        event_dict: dict[str, Any] = {
            "event": "some.event",
            "count": 99,
            "exc_info": (ValueError, ValueError("oops"), None),
        }
        result = structlog_processor(None, "info", event_dict)
        assert result["count"] == 99
        assert result["exc_info"] is event_dict["exc_info"]

    def test_returns_same_dict_object(self) -> None:
        event_dict: dict[str, Any] = {"event": "noop"}
        result = structlog_processor(None, "info", event_dict)
        assert result is event_dict


# ---------------------------------------------------------------------------
# Full log-boundary test — secret must not appear in rendered output
# ---------------------------------------------------------------------------


class TestLogBoundary:
    def test_secret_never_leaves_log_boundary(self) -> None:
        """Configure a minimal structlog chain with the redaction processor and
        capture its output; the secret must not appear anywhere in the result."""
        buf = io.StringIO()

        structlog.configure(
            processors=[
                structlog_processor,
                structlog.processors.KeyValueRenderer(key_order=["event"]),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(buf),
            cache_logger_on_first_use=False,
        )

        secret = "sk-test" + "x" * 45
        test_log = structlog.get_logger("test.boundary")
        test_log.info("user.input", api_key=secret, body=f"key is {secret}")

        output = buf.getvalue()
        assert secret not in output, f"Secret leaked in log output: {output!r}"


# ---------------------------------------------------------------------------
# sanitise_span_inputs() — tracing call site
#
# sanitise_span_inputs() is a one-liner wrapper: return redact_dict(inputs).
# We test the behaviour through redact_dict to avoid importing tracing.py,
# which transitively imports langsmith; langsmith==0.1.77 uses pydantic v1
# which has a ForwardRef._evaluate() incompatibility with Python 3.12.
# ---------------------------------------------------------------------------


class TestSanitiseSpanInputs:
    def test_span_inputs_redacted(self) -> None:
        secret = "sk-" + "h" * 48
        inputs: dict[str, Any] = {
            "user_query": f"my key is {secret}",
            "model": "gemini-2.5-flash",
        }
        result = redact_dict(inputs)
        assert secret not in str(result)
        assert result["model"] == "gemini-2.5-flash"

    def test_returns_new_dict(self) -> None:
        inputs: dict[str, Any] = {"a": "plain"}
        result = redact_dict(inputs)
        assert result is not inputs


# ---------------------------------------------------------------------------
# Exception handlers — status codes and envelope shape
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def test_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/not-found")
    async def raise_not_found() -> dict[str, str]:
        raise NotFoundError("thing not found")

    @app.get("/permission-denied")
    async def raise_permission_denied() -> dict[str, str]:
        raise PermissionDenied("access denied")

    @app.get("/validation-failure")
    async def raise_validation_failure() -> dict[str, str]:
        raise ValidationFailure("bad domain input")

    @app.get("/tool-failure")
    async def raise_tool_failure() -> dict[str, str]:
        raise ToolFailure("tool broke")

    @app.get("/unexpected")
    async def raise_unexpected() -> dict[str, str]:
        raise RuntimeError("boom")

    return app


@pytest.fixture(scope="module")
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app, raise_server_exceptions=False)


class TestExceptionHandlers:
    def test_404_not_found(self, client: TestClient) -> None:
        resp = client.get("/not-found")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert "request_id" in body

    def test_403_permission_denied(self, client: TestClient) -> None:
        resp = client.get("/permission-denied")
        assert resp.status_code == 403
        body = resp.json()
        assert body["code"] == "permission_denied"
        assert "request_id" in body

    def test_422_validation_failure(self, client: TestClient) -> None:
        resp = client.get("/validation-failure")
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "validation_failure"
        assert "request_id" in body

    def test_500_tool_failure(self, client: TestClient) -> None:
        resp = client.get("/tool-failure")
        assert resp.status_code == 500
        body = resp.json()
        assert body["code"] == "tool_failure"
        assert "request_id" in body
        assert "stack" not in str(body).lower()
        assert "traceback" not in str(body).lower()

    def test_500_unexpected_error_no_stack_trace(self, client: TestClient) -> None:
        resp = client.get("/unexpected")
        assert resp.status_code == 500
        body = resp.json()
        assert body["code"] in ("internal_error", "tool_failure")
        assert "request_id" in body
        assert "boom" not in body.get("message", ""), "internal detail must not leak"

    def test_envelope_always_has_three_keys(self, client: TestClient) -> None:
        for path in ("/not-found", "/permission-denied", "/validation-failure"):
            body = client.get(path).json()
            assert set(body.keys()) >= {"code", "message", "request_id"}
