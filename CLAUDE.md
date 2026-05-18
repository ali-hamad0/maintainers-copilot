# CLAUDE.md — Maintainer's Copilot (AIE Week 7)

This file is your contract. Read it at the start of every session. If a request asks you to violate something here, push back and explain which rule it breaks.

---

## 1. Mission 

Build an authenticated, tool-calling chatbot that helps an open-source maintainer triage closed issues. It classifies issues (bug / feature / docs / question) with three models compared on the same test set, extracts entities, summarises threads, and answers maintainer questions via advanced RAG over the project's docs and resolved issues. It carries memory across conversations. It is embeddable as a small React widget into a host site. Both eval suites fail CI on regression.

**Solo, 5 days, deadline Friday.** The architecture, the evals, and the ability to defend every line are the grade. Working code in a tangled codebase scores below slightly-worse code in a clean one.

---

## 2. Architecture (Mandatory Layered Layout)

```
backend/
  app/
    api/           # HTTP only. Routers. NO SQLAlchemy, NO Redis, NO external calls.
    services/      # Business logic. Transaction boundaries. Cache/memory invalidation.
    repositories/  # SQL only. NO HTTP errors. NO cache logic.
    domain/        # Pydantic domain models (distinct from ORM).
    infra/         # Adapters: Vault, MinIO, Redis, LLM, model_server, tracing, redaction.
    main.py        # FastAPI app, lifespan, mount routers.
    dependencies.py
    config.py      # Settings class only.
  alembic/         # Migrations. upgrade() and downgrade() both work.
  tests/
  pyproject.toml
  Dockerfile
chatbot/           # Streamlit admin/chat UI (separate service, separate deps).
widget/            # React + Vite, single JS bundle. Tailwind or vanilla CSS.
host/              # Nginx static container serving the demo host app.
modelserver/       # FastAPI inference server for classifier, NER, summariser.
prompts/           # Versioned prompt files. NO prompts in service code.
evals/             # Golden sets + harness.
docker-compose.yml
.env.example
ARCH.md DECISIONS.md RUNBOOK.md EVALS.md SECURITY.md
```

**Layer rule.** If you add a new endpoint, the diff touches `api/` + `services/` + maybe `domain/`. If it also touches `repositories/`, you wrote SQL. If `api/` imports SQLAlchemy, you broke the layout — refuse and refactor.

**Frontend/backend separation.** Each top-level service has its own `pyproject.toml`/`package.json` and its own `Dockerfile`. No shared `__init__.py` reaching across boundaries.

---

## 3. Compose Stack

`api` (FastAPI), `chatbot` (Streamlit), `widget` (static React bundle), `modelserver` (FastAPI), `host` (nginx demo), `migrate` (Alembic one-shot), `db` (postgres:16 + pgvector), `redis` (redis:7), `minio`, `vault` (dev mode).

`docker compose up` from a fresh clone after `cp .env.example .env` + filling the Vault root token must bring everything up cleanly.

---

## 4. Refuse-to-Boot Conditions (api service)

The api refuses to start if any of these are true:

1. Vault is unreachable.
2. Classifier weights file is missing.
3. The weights' SHA-256 does not match the value in the model card.
4. The tracing backend is misconfigured.
5. Any committed eval threshold in `eval_thresholds.yaml` is set to 0 or `disabled`.

These are startup assertions, not runtime checks. They live in `app/main.py` lifespan or a `bootcheck.py` invoked before `FastAPI(lifespan=...)`.

---

## 5. Engineering Standards (Non-Negotiable)

### Async all the way down
- Every route, tool, and external call is `async`.
- HTTP: `httpx.AsyncClient`, never `requests`.
- DB: SQLAlchemy 2.x async, never sync `Session`.
- LLM: `AsyncOpenAI` / `AsyncAnthropic`.
- `time.sleep` → `await asyncio.sleep`. CPU-bound work → `asyncio.to_thread`.

### Dependency injection
- Every shared resource is injected with `Depends()`. No module-level globals for engines, clients, models, or users.
- `get_db` uses `yield` inside `try/finally` so sessions close even on exception.
- Tests override with `app.dependency_overrides[...] = lambda: Fake()`. No monkeypatching of imports.

### Singletons via lifespan
- DB engine, ML models, embedder, LLM clients, shared `httpx.AsyncClient`, vector store handle: built once in `lifespan`, attached to `app.state`, disposed on shutdown.
- Per-request: DB session, transaction, current user.
- Per-call: anything derived from input.

### Settings
- ONE `Settings(BaseSettings)` class with `model_config = SettingsConfigDict(env_file=".env", extra="forbid")`.
- `extra="forbid"` is mandatory — typos in `.env` must fail at startup, not silently leave `None`.
- `get_settings()` wrapped with `@lru_cache(maxsize=1)`.
- `grep -rn 'os.getenv' app/` returns ZERO matches outside `config.py`.

### Pydantic at every boundary
- HTTP request bodies, tool inputs, LLM structured outputs, webhook payloads — all Pydantic.
- `response_model` on every endpoint is a DTO, NOT the ORM model. Password hashes and internal flags do not leak.
- ORM models live in `repositories/` adjacent code; domain models in `domain/`. Conversion happens in `services/`.

### Errors
- Three layers: timeouts on every external call, retries with exponential backoff for transient errors only (use `tenacity`), structured `ToolError(error, retryable)` returned to the agent — NOT raised.
- Custom exception hierarchy in `domain/exceptions.py`: `AppError`, `NotFoundError`, `PermissionDenied`, `ToolFailure`. ONE `@app.exception_handler` in `api/` maps them to HTTP responses with `code` + `request_id`. Users NEVER see a stack trace.
- A 500 with `{"error":"something went wrong"}` is a leak, not a feature. Log the trace internally with `request_id` and the trace_id from the tracing backend.
- `except:` and `except: pass` are banned. Catch specific exceptions.

### Caching
- `@lru_cache(maxsize=1)` for `get_settings`, model-path resolvers, and other pure helpers.
- `cachetools.TTLCache` + `asyncio.Lock` (double-checked) for external responses (weather, embeddings of common queries). TTL value is a documented decision, not a guess.
- Anything that must survive restarts or be shared across replicas → Redis.

### Tests
You will NOT hit 100% coverage and should not try. What MUST be tested:
- Every Pydantic schema (one happy, one rejection per schema).
- Every tool (mock the LLM/HTTP, assert the structured return shape, including the `ToolError` path).
- One end-to-end happy path through the agent with all external calls mocked.
- The redaction test (a fake API key in input never appears unredacted in logs, traces, or memory writes).
- Auth: 401 with no token, 403 with wrong role, 200 with a valid one.

---

## 6. Security (from Casaba Agentic AI Security Spec + bootcamp standards)

### Secrets
- Vault holds: LLM API keys, JWT signing key, DB password, MinIO creds, tracing backend keys.
- `.env` holds ONLY the Vault root token and ports.
- `grep -ri 'sk-' app/` and `grep -ri 'password' app/` return zero matches outside `app/infra/vault.py`.
- Pre-commit: `gitleaks`. No exceptions.

### Tool safety
- Tools are atomic, single-purpose, with Pydantic `args_schema`. NO generic "run SQL" or "make HTTP request" tools.
- Per-agent allowlists, default deny.
- High-impact tools (write_memory, anything that mutates state) require an explicit user-visible confirmation in the chat flow, or are admin-only.
- `write_memory` is the ONLY way long-term memory gets written. No auto-writes from the agent loop.

### Redaction
- `app/infra/redaction.py` runs before any log line, trace span, or memory write leaves the service boundary.
- Patterns are documented in `SECURITY.md` with the reasoning per pattern.
- The redaction test is mandatory and runs in CI on every push.

### Memory hygiene
- Long-term memory rows carry provenance metadata: actor (user_id), source (which tool wrote it), conversation_id, timestamp.
- Every long-term write produces an `audit_log` row.
- Memory stores are segmented by user. No cross-user retrieval.
- Agent outputs re-ingested as memory carry a lower trust score than primary source material.

### Origin allowlisting (the widget)
- CORS allowlist is enforced from the `widget.allowed_origins` field in Postgres, not from a hardcoded env var.
- Embed route sets `Content-Security-Policy: frame-ancestors <allowed origins>`.
- Friday demo MUST show: widget loads on allowed host; widget is blocked on a non-allowlisted host (real browser network + console output).

### Auth
- `fastapi-users` with JWT. Email + password registration.
- JWT signing key from Vault at startup.
- 401 = no/expired/bad token. 403 = authenticated but not allowed. Returning 403 for missing token is a bug.
- Refresh tokens implemented OR refresh mechanism described clearly in `ARCH.md`.

---

## 7. Observability

### Tracing
- Pick ONE backend (LangSmith is the bootcamp default from Week 4; Langfuse and Phoenix are also fine). Choice goes in `DECISIONS.md`.
- Every LLM call, tool call, and RAG retrieval is a span.
- A conversation is a trace tree rooted at the user message.
- Span attributes: model_name, prompt/completion token counts, latency_ms, tool_input/tool_output AFTER redaction.
- The trace_id is in every structured log line for the same request so logs ↔ traces are joinable.
- Friday demo: open the tracing UI and walk through a real trace tree including an error path.

### Logging
- `structlog` with JSON renderer in prod, key-value in dev.
- `log.info("event.name", key=value)`, NEVER `print()` and NEVER f-string formatting in the message field.
- Every log line includes `trace_id`, `request_id`, `user_id` when authenticated.
- Logs go to a file AND stdout. `print` shows up in code review as "did not understand logging."

### Audit log
A Postgres table written on: role changes, memory writes, widget config changes, conversation deletions. Columns: id, actor_id, action, target_type, target_id, timestamp, request_id, payload (jsonb, redacted).

---

## 8. Data, Persistence, Migrations

- Postgres 16 + pgvector. ALL schema changes go through Alembic.
- Every PR that changes a model has a paired migration with a working `downgrade()`. "Just delete the volume" is not a strategy.
- `migrate` container runs `alembic upgrade head` and exits before `api` boots.
- pgvector indexes are HNSW, not IVFFlat. See `Advanced_RAG_Techniques_Guide.txt` §7b.
- MinIO holds: model artefacts (or a manifest with SHA-256), `eval_report.json` from every CI run, training plots, per-conversation retrieved-chunks snapshots for the last N conversations (define N in `ARCH.md`).

---

## 9. Code Style & Naming

- Python 3.12, `uv` for env + lockfile. NEVER `pip install` directly. Commit `uv.lock`.
- `ruff` (lint + format), `black`, `isort`, `mypy --strict`. Configure in `pyproject.toml`.
- Line length 100. Double quotes. Type hints on every public function.
- Imports in 3 groups: stdlib, third-party, local. Blank line between.
- snake_case for variables/functions/modules. PascalCase for classes. UPPER_SNAKE for constants. Booleans read as questions (`is_active`, `has_permission`).
- File names describe what the file does. `utils.py`, `helpers.py`, `stage1.py` are banned. `redaction.py`, `priority_prompt.py`, `hybrid_retriever.py` are fine.
- Functions/methods ≤ 50 lines as a soft cap. Files ≤ 300 lines as a soft cap. Above that, split along seams of responsibility.
- One prompt per file in `prompts/`. NOT `prompts.py` with five templates.

---

## 10. Branches, Commits, PRs

- Branches: `feature/<short>`, `bugfix/<short>`, `refactor/<short>`, `docs/<short>`, `test/<short>`, `chore/<short>`. Lowercase + hyphens. NO underscores.
- Commits: Conventional Commits. `<type>(<scope>): <imperative summary under 72 chars>`. No trailing period.
  - Good: `feat(rag): add cross-encoder rerank over top-20`
  - Bad: `updates`, `fixed stuff`, `wip`
- PR title: `[FEATURE]` / `[BUGFIX]` / `[REFACTOR]` etc. + imperative description.
- Never commit to `main`. Open a PR, even solo — review your own diff.

---

## 11. Per-Phase Review Protocol (THIS IS THE WORKFLOW)

At the END of every phase, before moving on, run this protocol with Claude Code:

1. **Re-read this CLAUDE.md.** Yes, again.
2. **Re-read the phase's section in `PROJECT_PLAN.md`.** Get the exact deliverables and the review checklist.
3. **Run the local checks**: `ruff check`, `black --check`, `mypy`, `pytest -q`, `docker compose up` from a cold start.
4. **Walk the diff.** For each new/changed file ask:
   - What does it do, in one sentence?
   - Which layer does it belong in? Did I put it there?
   - Does it import across a layer it shouldn't?
   - Is there an `os.getenv`, `print`, bare `except`, hardcoded secret, or sync HTTP call I missed?
5. **Check against the phase's "Defend Your Code" questions.** If I cannot answer one out loud, that's my next task.
6. **Update `DECISIONS.md`.** Every choice that has a number behind it (embedding model, chunk size, λ for MMR, retry count, TTL) gets one line + the number.
7. **Commit, push, watch CI.** If CI is red, the phase isn't done.

If a step fails, you DO NOT move to the next phase. Fix the gap, then re-run the protocol from step 3.

---

## 12. The Rules That Cannot Be Bent

1. NO vibe coding. If you can't explain a line on Friday, delete it now.
2. The ARCHITECTURE is the grade. The EVALS are the grade.
3. Every decision is backed by a NUMBER. Embedding model, chunk size, retrieval weight, deployment choice. Number, not vibe.
4. LOGS are REDACTED. TRACES are REAL. A test proves the first, a demo proves the second.
5. Three models, three numbers, one production choice. Defend the choice.

When in doubt, refuse and ask. When asked to add scope, refuse and say "after submission."

---

## 13. References (in this repo)

Drop these source files into a `docs/_references/` folder (gitignored — they are study material, not artefacts):

- `AIE_Week7_Maintainers_Copilot_v4.txt` — the brief. The grade.
- `AIE_Bootcamp_Coding_Guidelines.txt` — style, security, testing, naming.
- `Engineering_Standards_Companion_Guide.txt` — async, DI, lifespan, Settings, errors.
- `code_review_guidelines.txt` — defend-every-line + project structure.
- `week3_code_review_lessons_learned.txt` — RAG basics, project structure, OOP.
- `week4_code_review_lessons_learned.txt` — auth, persistence, agents, tracing.
- `Advanced_RAG_Techniques_Guide.txt` — the eight techniques + recommended sequencing.
- `guides.txt` — Casaba Agentic AI Security Spec (Foundational + Operational rules).
- `PROJECT_PLAN.md` — the phase-by-phase plan that this CLAUDE.md governs.

End of CLAUDE.md.
 