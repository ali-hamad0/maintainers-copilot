"""LangSmith tracing setup.

configure_tracing() sets the env vars that langsmith reads automatically,
then emits one startup span so the tracing UI shows a real trace on boot.

sanitise_span_inputs() is the second redaction call site (CLAUDE.md §6).
All code that builds explicit span metadata MUST pass it through this
function before handing it to @traceable or RunTree.
"""

import os
from typing import Any

import structlog
from langsmith import traceable

from app.infra.redaction import redact_dict

log = structlog.get_logger(__name__)


def configure_tracing(api_key: str, project: str, enabled: bool = True) -> None:
    """Wire LangSmith by setting the env vars it reads at import time."""
    if not enabled or not api_key or api_key.startswith("ls-placeholder"):
        log.warning("tracing.disabled", reason="no valid LangSmith API key configured")
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        return

    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = api_key
    os.environ["LANGSMITH_API_KEY"] = api_key
    os.environ["LANGCHAIN_PROJECT"] = project
    os.environ["LANGSMITH_PROJECT"] = project
    log.info("tracing.configured", backend="langsmith", project=project)


def sanitise_span_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets from span inputs before emitting to the tracing backend.

    Call this on any dict you pass as explicit metadata or inputs to @traceable
    or RunTree.  The structlog processor covers logs automatically; this covers
    the tracing call site (second of THREE call sites — see redaction.py).
    """
    return redact_dict(inputs)


@traceable(name="api.startup_health")
async def emit_startup_span(environment: str) -> dict[str, str]:
    """Single startup span that proves the tracing backend is wired."""
    return {"event": "api_started", "environment": environment}
