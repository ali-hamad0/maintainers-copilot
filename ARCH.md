# ARCH.md — Architecture

## Overview

Maintainer's Copilot is a layered FastAPI backend that triages GitHub issues
for an open-source maintainer using a three-model classifier, hybrid RAG
retrieval, and an authenticated tool-calling chatbot.

```
browser / widget
      │
      ▼
  nginx host  ←──────────── static React bundle (widget/)
      │
      ▼
FastAPI API (backend/app/)
  ├── api/         HTTP only: routes, request parsing, response shaping
  ├── services/    Business logic: memory, retrieval, auth lifecycle
  ├── repositories/ SQL only: ORM queries, pgvector, audit log
  ├── domain/      Pydantic models, exception hierarchy
  └── infra/       Adapters: Vault, Redis, Embedder, Reranker, redaction, tracing
      │
      ├── PostgreSQL 16 + pgvector  (tables: users, conversations, messages,
      │                              memory_long_term, chunks, eval_runs, audit_log)
      ├── Redis 7                   (short-term memory; keys memory:short:{conv_id})
      ├── MinIO                     (model weights, eval reports, chunk snapshots)
      └── Vault (dev mode)          (JWT secret, DB password, API keys)
```

## Layer Boundaries

### Registration end-to-end
1. `POST /auth/register` → `api/auth_router.py` (fastapi-users router)
2. fastapi-users `UserManager.on_after_register` hook → `services/` side-effect
3. `SQLAlchemyUserDatabase.create()` → INSERT INTO `users`

### Login end-to-end
1. `POST /auth/jwt/login` → fastapi-users BearerTransport
2. `JWTStrategy.write_token(user)` → signs with `app.state.secrets.jwt_secret` (Vault)
3. Response: `{"access_token": "...", "token_type": "bearer"}`

### Chat end-to-end (Phase 13)
1. `POST /chat` with `Authorization: Bearer <token>`
2. `current_active_user` dependency decodes JWT → looks up user in DB
3. `MemoryShortService.get_all(conversation_id)` → Redis LRANGE
4. Agent loop: tool calls (retrieve, classify, summarise, write_memory)
5. `MemoryShortService.append(conversation_id, message)` → Redis RPUSH + EXPIRE

### Memory write end-to-end
1. Agent calls `write_memory` tool (Phase 13)
2. Tool calls `MemoryLongService.write(...)` after `redact_dict(payload)`
3. `MemoryRepo.insert()` → INSERT memory_long_term + INSERT audit_log (same session)

## Memory Model

### Short-term (Redis)
- **Key:** `memory:short:{conversation_id}`
- **Type:** Redis list (RPUSH / LRANGE)
- **TTL:** 1800 s (30 min), refreshed on every write — see D-P12-01
- **Scope:** Single conversation. Cleared explicitly or on TTL expiry.

### Long-term (pgvector)
- **Table:** `memory_long_term`
- **Type:** Episodic — see D-07
- **Columns:** `user_id`, `embedding vector(768)`, `payload jsonb`,
  `provenance jsonb`, `trust_score float`, `content text`, `created_at`
- **Retrieval:** cosine similarity via pgvector HNSW index (`<=>` operator)
- **Segmentation:** all queries filter on `user_id`; cross-user retrieval is
  structurally impossible (no query path exists without a `user_id` bind param)
- **Audit:** every write produces one `audit_log` row in the same DB transaction

### Trust scores
| Source | Score | Rationale |
|--------|-------|-----------|
| Direct maintainer input | 1.0 | Primary source |
| Agent-reingested output | 0.7 | Downstream inference; lower confidence |

## Trace Tree Shape

Every user message starts a LangSmith trace root. Child spans:
- `llm.call` — Gemini generation (model, tokens, latency_ms)
- `tool.retrieve` — hybrid RAG retrieval (query, top-k, hit@5)
- `tool.classify` — modelserver /v1/classify call
- `tool.summarise` — modelserver /v1/summarise call
- `tool.write_memory` — memory write (payload after redaction)

`trace_id` is propagated into every structlog line via contextvars so logs ↔
traces are joinable.

## Widget Config & Origin Allowlist

Widget CORS is enforced from `widget_config.allowed_origins` in Postgres
(not from a hardcoded env var). The embed route sets:
```
Content-Security-Policy: frame-ancestors <allowed_origins joined by space>
```
The allowed-origins list is loaded per-request from the DB; changes take effect
immediately without a restart.

## Refresh Token Mechanism

**Phase 12 decision:** no server-issued refresh tokens. See D-P12-08.

The access token has a 60-minute lifetime (D-P12-02). When it expires, the
client receives a `401 Unauthorized`. The correct flow is:

1. Client detects 401.
2. Client redirects the user to `POST /auth/jwt/login` with email + password.
3. A new access token is returned.

**Why no refresh tokens now:** fastapi-users' stateless `JWTStrategy` does not
issue refresh tokens by design. Adding them requires switching to
`DatabaseStrategy` (a new `access_tokens` table, `alembic upgrade`, revocation
logic). For a triage tool with 60-minute sessions, the re-login UX is
acceptable. If longer sessions are needed, the upgrade path is:
1. Add `AccessToken` ORM model.
2. Switch `JWTStrategy` → `DatabaseStrategy`.
3. Issue `refresh_token` alongside `access_token` at login.
4. Implement `POST /auth/jwt/refresh` that validates the refresh token and
   issues a new access token.
