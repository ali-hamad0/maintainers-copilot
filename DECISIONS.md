# DECISIONS.md — Maintainer's Copilot

Every decision with a number behind it. Updated at the end of each phase.

## Phase 1 — Repo Skeleton + CLAUDE.md

- **Layered architecture**: Backend split into `api/`, `services/`, `repositories/`, `domain/`, `infra/` per CLAUDE.md §2 (enforced at code review)
- **Service separation**: Three top-level services with separate `pyproject.toml`: backend, modelserver, chatbot (each has own Dockerfile)
- **Python version**: 3.12 (pinned in all `pyproject.toml`)
- **Dependency manager**: `uv` for all Python services (lockfiles committed)
- **Pre-commit hooks**: ruff, black, isort, mypy, gitleaks (CI gate on push)
- **Config pattern**: Pydantic `Settings` with `extra="forbid"` in `config.py` (catches typos)
- **Secrets**: Vault root token only in `.env`; all other secrets from Vault at startup (Phase 2)
- **Ports**: API=8000, Chatbot=8501, Widget=3000, ModelServer=8001, Host=8080, Vault=8200, PostgreSQL=5432, Redis=6379, MinIO=9000
- **Documentation**: README with full setup/run/deploy guide; stub docs for architecture, decisions, runbook, evals, security
- **LLM Provider**: Google Gemini with Flash/Pro split (set default to `gemini`, use `GEMINI_API_KEY` in .env)
- **Embedding Model**: `gemini-embedding-001` with 768-dim for pgvector (configured in .env)
- **Dependencies**: Replaced `openai` and `anthropic` with `google-genai==0.3.0`

## D-01 LLM Provider
**Choice:** Google Gemini via `google-genai` SDK (pinned at `0.3.0`)  
**Models:**
- Cheap: `gemini-2.5-flash` — tool calls, classification baseline, short answers
- Strong: `gemini-2.5-pro` — final answer synthesis, RAG generation
**Why Gemini:** API key available; Flash/Pro split enables cost-quality routing  
**Async support:** `google-genai` async via streaming and async iteration  
**Config:** `LLM_PROVIDER=gemini`, `GEMINI_API_KEY` in .env (from Vault Phase 2)  
**Phase 1 changes:**
- Removed `openai==1.6.1` and `anthropic==0.7.1` from `backend/pyproject.toml`
- Added `google-genai==0.3.0`
- Updated `backend/app/config.py`: `llm_provider` defaults to `"gemini"`, uses `gemini_api_key` field
- Updated `.env.example`: `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=AIza_xxxxx...`

## D-02 Embedding Model
**Choice:** `gemini-embedding-001`  
**Dimension:** 768 — locked into Alembic migration `Vector(768)`  
**Why gemini-embedding-001:** Paired with Gemini LLM for API consistency; 768-dim is reasonable for pgvector HNSW  
**Benchmark:** Phase 8 (Wednesday) — compare against `text-embedding-3-small` (1536-dim) on 25-question RAG golden set (hit@5 metric)  
**Config:** `EMBEDDING_MODEL=gemini-embedding-001`, `EMBEDDING_DIM=768` in .env  
**Phase 1 changes:**
- Added `embedding_model` and `embedding_dim` fields to `backend/app/config.py`
- Updated `.env.example` with embedding configuration
**Decision finalised:** Phase 8 (benchmark, confirm choice, or switch)

## D-03 Tracing Backend
**Choice:** LangSmith  
**Why:** Bootcamp default; native support for Google GenAI via `langsmith` SDK; trace tree UI covers LLM + tool + RAG spans  
**Alternative considered:** Langfuse — rejected due to less native Google GenAI integration

## D-04 OSS Repo for Issues
**Choice:** TBD — decide Monday morning  
**Criteria:** ≥500 closed issues, maintainer-applied labels mappable to bug/feature/docs/question, active test set  
**Label mapping:** TBD after repo is chosen

## D-05 Fine-tuned Classifier Base Model
**Choice:** `distilbert-base-uncased`  
**Why:** 66M params — trains on Colab free tier; clear freeze policy (last 2 blocks + head)  
**Alternative:** `bert-base-uncased` — larger, marginal gains for this task

## D-06 Classical ML Baseline
**Choice:** TBD Phase 5 — likely TF-IDF + LogisticRegression  
**Decision finalised:** Phase 5

## D-07 Memory Type
**Choice:** TBD — options: episodic / semantic / procedural  
**Decision finalised:** Phase 10 (Wednesday noon)

## D-08 CI Platform
**Choice:** GitHub Actions  
**Why:** No extra tooling; matrix jobs for lint + type-check + build + eval suites

## D-09 Vector Index Type
**Choice:** HNSW (not IVFFlat)  
**Why:** Per brief; better query performance; no training phase needed  
**Parameters:** `m=16, ef_construction=64` (defaults)

## D-10 Redis TTL for Short-term Memory
**Choice:** TBD Phase 12  
**Candidates:** 3600s (1h), 86400s (1 day)  
**Decision finalised:** Phase 12

## Phase 2 — Compose Stack + Vault + Migrate + Tracing Wired

### D-P2-01 Vault Secret Paths
**Paths:** `secret/jwt`, `secret/db`, `secret/minio`, `secret/tracing`, `secret/gemini`, `secret/openai`, `secret/anthropic`
**KV version:** 2 (supports versioning + metadata)
**Why v2:** Enables secret versioning and soft-delete; no performance penalty for dev mode

### D-P2-02 Migrate Container Strategy
**Choice:** Separate `migrate` one-shot container that runs `alembic upgrade head` and exits 0
**Why separate:** If migrations run inside `api`'s entrypoint, a failed migration kills the running server and makes the error harder to surface. A separate container exits with a clear code; docker-compose `depends_on: condition: service_completed_successfully` blocks `api` until migrations are clean. "Just delete the volume" is not a strategy.

### D-P2-03 Alembic Driver
**Choice:** asyncpg for both the app engine and Alembic migrations (via `asyncio.run(run_migrations_online())`)
**Why:** Keeps one driver in the image; no need for psycopg2/psycopg3 as a separate dep just for migrations. Alembic 1.13+ async support is stable.

### D-P2-04 HNSW Parameters (confirmed D-09)
**m=16, ef_construction=64** — pgvector defaults; good recall/build-time tradeoff at 768 dim.
**Benchmark:** Phase 8 will measure hit@5 and tune if needed. IVFFlat rejected: requires training (`IVFFLAT_LISTS`) and query-time `SET ivfflat.probes`; HNSW needs neither.

### D-P2-05 structlog Renderer
**Prod (json):** `JSONRenderer` — machine-parseable, Datadog/CloudWatch compatible
**Dev (json or key-value):** `ConsoleRenderer` when `LOG_FORMAT=dev` — human-readable for local development
**Every log line carries:** `trace_id`, `request_id`, `user_id` (injected via structlog contextvars by middleware in Phase 12)

### D-P2-06 LangSmith Tracing Configuration
**How wired:** Vault reads `secret/tracing.api_key` → set `LANGCHAIN_API_KEY` + `LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_PROJECT` at startup
**Startup span:** `emit_startup_span()` decorated with `@traceable` emits one real span per boot — confirms the tracing pipeline end-to-end
**Disabled gracefully:** If key is placeholder (`ls-placeholder-*`), tracing is skipped without crashing

### D-P2-07 eval_thresholds.yaml Initial Values
**classification.accuracy:** 0.70 (minimum for Phase 7 CI gate)
**classification.f1_macro:** 0.65
**rag.hit_at_5:** 0.70
**rag.faithfulness:** 0.75
**rag.answer_relevance:** 0.70
**Why these numbers:** Conservative baselines that a fine-tuned distilbert + HNSW retrieval should exceed; tightened in Phase 7/10 after benchmarking.

## Phase 3 — Dataset + Splits + Training Notebook

### D-P3-01 OSS Repository Chosen
**Choice:** `pandas-dev/pandas`
**Why:**
- >20 000 closed issues as of May 2026 — large enough for a meaningful train/val/test split with balanced classes
- Maintainers apply structured labels that map cleanly onto the four target classes (see D-P3-02)
- Actively maintained since 2009; issues span a wide date range, enabling a clean time-based split without data leakage
- Highly popular project — issues are well-formed (title + body), reducing noise vs. less active repos

### D-P3-02 Label Mapping (pandas labels → {bug, feature, docs, question})
Priority order when an issue carries multiple mapped labels: **bug > feature > docs > question**.
Issues with no mappable label are discarded entirely (not assigned a fallback class).

| pandas label(s) | Mapped class |
|---|---|
| `Bug`, `Regression`, `Crash` | `bug` |
| `Enhancement`, `New Feature`, `Performance`, `Refactor` | `feature` |
| `Docs`, `Documentation` | `docs` |
| `Question`, `Usage Question` | `question` |

**Unmapped labels (discarded):** `Good First Issue`, `Help Wanted`, `Needs Discussion`,
`Needs Info`, `Needs Triage`, `Typing`, `Testing`, `CI`, `Code Style`, `API Design`,
`Deprecation`, `Duplicate`, `Invalid`, `Wontfix`, `Contributor Experience`.

**Rationale for priority order:** Bugs carry the highest triage urgency; a bug+docs issue is a bug
that also needs a docs fix. Feature vs. docs is separated by whether the request changes behaviour
(feature) or text only (docs). Questions are lowest priority because they are informational and do
not drive engineering work.

### D-P3-03 Split Strategy
**Method:** Time-based split on `closed_at` (not random). Issues are sorted ascending by close date.
**Why time-based:** Avoids leaking future knowledge into the training set; mirrors real deployment
where the classifier sees issues newer than anything it was trained on. Random splits would allow
the model to memorise recurring phrases across time.
**`random_state=42` usage:** Used only inside the training notebook for DataLoader shuffling; the
split boundaries themselves are deterministic time boundaries.

**Fractions (applied to all labeled issues, most-recent-last order):**

| Split | Fraction | Purpose |
|---|---|---|
| Train | ~63% (oldest) | Fine-tune DistilBERT and classical baseline |
| Val | 12% | Hyperparameter tuning + early stopping |
| Test | 15% | Final held-out evaluation; feeds CI gate |
| RAG corpus | 10% (newest) | Dense retrieval index; excluded from classifier splits |

**Time boundary invariant (validated by `split_issues.py`):**
`max(train.closed_at) < min(val.closed_at) < min(test.closed_at) < min(rag.closed_at)`

### D-P3-04 RAG Corpus Exclusion Rationale
The 10% most-recent issues are reserved for the RAG retrieval index and **excluded** from the
classifier train/val/test splits.
**Why exclude:** The RAG corpus is retrieved at inference time. Including those issues in the
classifier training set would give the model implicit access to "future" patterns during training —
inflating val/test metrics and undermining the CI gate as a leakage detector. Keeping the corpus
strictly newer than test also means the retriever indexes information the classifier has never seen,
which better reflects the production scenario (new issues arrive daily).

### D-P3-05 JSONL Schema per Row
```json
{"id": 12345, "text": "TITLE\n\nBODY", "label": "bug", "closed_at": "2023-01-15T10:23:00Z", "split": "train"}
```
- `text` concatenates title and body with `\n\n` — standard for sequence-classification fine-tuning
- `label` is one of `bug | feature | docs | question`
- `closed_at` is the raw ISO 8601 string from the GitHub API (UTC, `Z` suffix)
- `split` field is included in every row for traceability when rows are later merged

### D-P3-06 DistilBERT Freeze Policy
**Freeze:** All DistilBERT layers **except** the final transformer block (layer index 5 of 6) and
the classification head.
**Why one block:** Unfreezing only the last block (~7M/66M params) is enough to adapt the
task-specific representation while keeping training time under 20 min on Colab T4. Unfreezing all
6 blocks risks catastrophic forgetting of general language representations given ~10k–15k training
examples.
**Hyperparameters:** lr=2e-5, batch=32, epochs=5, optimizer=AdamW, weight_decay=0.01,
warmup_steps=500
**Why lr=2e-5:** Standard fine-tuning rate for BERT-family models; higher rates cause instability
with partially frozen weights.
**Why warmup_steps=500:** At batch=32 and ~10k examples → ~312 steps/epoch; 500 warmup steps
covers ~1.6 epochs, giving the classifier head time to stabilise before the unfrozen transformer
block adjusts.

## Phase 4 — Fine-Tuned Classifier + Model Card

(To be filled)

## Phase 5 — Classical ML + LLM Baselines + Three-Way Comparison

(To be filled)

## Phase 6 — NER + Summariser Endpoints

(To be filled)

## Phase 7 — Classification Golden Set + CI Gate #1

(To be filled)

## Phase 8 — Corpus + Embeddings + Smart Chunking

(To be filled)

## Phase 9 — Hybrid + Rerank + Query Transformation + Metadata Filters

(To be filled)

## Phase 10 — RAG Golden Set + CI Gate #2

(To be filled)

## Phase 11 — Redaction Layer + Exception Handling Refactor

(To be filled)

## Phase 12 — Auth + Memory

(To be filled)

## Phase 13 — Tool-Calling Chatbot

(To be filled)

## Phase 14 — Streamlit App + React Widget + Host App + Origin Allowlist

(To be filled)

## Phase 15 — Polish, Docs, CI Green, Submission, Demo

(To be filled)
