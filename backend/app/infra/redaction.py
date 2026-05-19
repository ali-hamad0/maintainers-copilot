"""Redaction pipeline — ONE implementation, three call sites.

Call sites:
  1. structlog_processor  → logging_setup.py (every log line, automatically)
  2. sanitise_span_inputs → tracing.py (before span attributes reach LangSmith)
  3. write_memory tool    → tools/write_memory.py (Phase 13, before DB write)

Never add a fourth call site.  Fix the pattern list here instead.
"""

import re
from collections.abc import MutableMapping
from typing import Any

# More specific patterns MUST come before broader ones.
# e.g. Anthropic (sk-ant-) before generic OpenAI (sk-).
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Anthropic secret keys — sk-ant-api03-... format
    (re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"), "[REDACTED_ANTHROPIC_KEY]"),
    # OpenAI secret and project keys — sk-... / sk-proj-... (hyphens allowed)
    (re.compile(r"sk-[A-Za-z0-9\-_]{20,}"), "[REDACTED_OPENAI_KEY]"),
    # GitHub PATs, fine-grained tokens, OAuth tokens, and refresh tokens
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36}"), "[REDACTED_GITHUB_TOKEN]"),
    # AWS IAM access key IDs — always start AKIA
    (re.compile(r"AKIA[A-Z0-9]{16}"), "[REDACTED_AWS_ACCESS_KEY]"),
    # JWTs — three base64url segments, first two starting with eyJ
    (
        re.compile(r"eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]*"),
        "[REDACTED_JWT]",
    ),
    # Generic RFC 5321 email addresses
    (
        re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
        "[REDACTED_EMAIL]",
    ),
    # IPv4 addresses - covers 0.0.0.0-255.255.255.255
    (
        re.compile(
            r"\b(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\."
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\."
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\."
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
        ),
        "[REDACTED_IPV4]",
    ),
    # password=<value> in query strings, form bodies, and config dumps
    (re.compile(r"(?i)(password\s*=\s*)\S+"), r"\1[REDACTED]"),
    # HTTP Authorization header — Bearer, Basic, token schemes
    (re.compile(r"(?i)(Authorization:\s*)\S+(?:\s+\S+)?"), r"\1[REDACTED]"),
]


def redact(text: str) -> str:
    """Apply all redaction patterns to *text* and return the sanitised string."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *data* with all nested string values redacted."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = redact(value)
        elif isinstance(value, dict):
            result[key] = redact_dict(value)
        else:
            result[key] = value
    return result


def structlog_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor — redacts secrets from every string value.

    Must be first in the shared_processors chain so secrets cannot escape
    via any downstream processor (JSON serialiser, console renderer, etc.).
    """
    for key in list(event_dict.keys()):
        value = event_dict[key]
        if isinstance(value, str):
            event_dict[key] = redact(value)
    return event_dict
