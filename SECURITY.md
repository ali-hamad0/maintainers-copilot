# SECURITY.md — Safety & Compliance

## Redaction Patterns

All patterns live in `backend/app/infra/redaction.py`.  They are applied in the
order listed below; more specific patterns are checked first to prevent a less
specific pattern from consuming part of a secret and leaving the rest visible.

| Pattern | Regex | Why |
|---|---|---|
| **Anthropic keys** | `sk-ant-[A-Za-z0-9\-_]{20,}` | Checked before the generic `sk-` pattern because it is a strict prefix of the OpenAI pattern; reversing the order would redact only the `sk-` portion and leak `ant-…`. |
| **OpenAI keys** | `sk-[A-Za-z0-9]{20,}` | Covers both classic `sk-` and project-scoped `sk-proj-` keys; any leaked key gives full API access and runs up the operator's bill. |
| **GitHub tokens** | `gh[pousr]_[A-Za-z0-9]{36}` | Covers personal (ghp), OAuth (gho), user-to-server (ghu), server-to-server (ghs), and refresh (ghr) tokens; a leaked token can read/write repositories and trigger Actions. |
| **AWS access key IDs** | `AKIA[A-Z0-9]{16}` | AKIA is the canonical prefix for long-lived IAM access keys; exposure allows lateral movement across AWS services. |
| **JWTs** | `eyJ…\.eyJ…\.[A-Za-z0-9\-_]*` | JWTs carry user identity and role claims; a stolen JWT bypasses authentication for its entire lifetime and cannot be revoked without a key rotation. |
| **Email addresses** | RFC 5321 local-part + domain | Emails are PII under GDPR/CCPA; logging them without consent creates a compliance risk and can facilitate phishing if logs are breached. |
| **IPv4 addresses** | Standard octet notation | Internal IP addresses reveal network topology; external IPs are personal data in many jurisdictions. |
| **`password=…`** | `(?i)password\s*=\s*\S+` | Passwords appear in query strings, form bodies, and accidental debug-log dumps; the key is preserved so the log remains useful for debugging. |
| **Authorization headers** | `(?i)Authorization:\s*…` | Bearer tokens, Basic credentials, and API tokens all travel in this header; the header name is preserved so log correlation still works. |

Three call sites, ONE implementation (`redact()` / `redact_dict()`):

1. **structlog processor** (`logging_setup.py`) — runs on every log line before any renderer.
2. **`sanitise_span_inputs()`** (`tracing.py`) — must be called before passing explicit metadata to `@traceable` or a LangSmith `RunTree`.
3. **`write_memory` tool** (`tools/write_memory.py`, Phase 13) — applied to the memory content before the DB write and the audit-log row.

---

## Refuse-to-Boot Conditions

The `api` service calls `bootcheck.run_all()` in its lifespan before accepting
traffic.  It refuses to start if any of the following are true:

1. **Vault unreachable** — `assert_vault_reachable()` fails.
2. **Classifier weights file missing** — the SHA-256 manifest path resolves to nothing on the model server.
3. **Weights SHA-256 mismatch** — the file hash does not match the value in `model_card.md`.
4. **Tracing backend misconfigured** — the LangSmith API key is a placeholder (`ls-placeholder…`).
5. **Eval threshold zeroed or disabled** — any value in `eval_thresholds.yaml` equals `0` or `"disabled"`.

---

## Origin Allowlist Mechanism

CORS and `Content-Security-Policy: frame-ancestors` are enforced from the
`widget_origins` table in Postgres, not a hardcoded env var.  The embed route
reads the allowlist at request time; adding an origin requires a DB row, not a
redeploy.

---

## Vault Contents

| Path | Contents | Notes |
|---|---|---|
| `secret/data/jwt` | `secret` — JWT signing key | Used by `fastapi-users` at startup |
| `secret/data/db` | `password` — Postgres password | Never in `.env` |
| `secret/data/minio` | `access_key`, `secret_key` | For model artefact storage |
| `secret/data/tracing` | `api_key` — LangSmith API key | Also sets `LANGCHAIN_API_KEY` |
| `secret/data/gemini` | `api_key` — Gemini API key | Embedder + LLM judge |

`.env` holds **only** `VAULT_ADDR`, `VAULT_ROOT_TOKEN`, and service ports.

---

## Threat Model Summary

| Threat | Mitigation |
|---|---|
| **Prompt injection / XPIA** | Tool allowlist (default deny); high-impact tools require explicit confirmation; `write_memory` is the only path for long-term memory writes. |
| **Lethal trifecta** (tool + memory + retrieval poisoning) | Memory rows carry `trust_score`; agent re-ingested content scores lower than primary sources; memory is segmented per user. |
| **Tool poisoning** | All tool schemas are Pydantic-validated; no generic "run SQL" or "make HTTP request" tools. |
| **Secret leakage in logs/traces** | Redaction processor is first in the structlog chain; `sanitise_span_inputs()` wraps all explicit trace metadata. |
| **Cross-user memory read** | Memory queries include `WHERE user_id = :uid`; no admin bypass. |

---

## Not Covered

- DoS / rate-limiting (deferred to infra/CDN layer).
- Supply-chain attacks against `uv.lock` pinned dependencies (outside scope).
- Physical access to Vault storage backend.
