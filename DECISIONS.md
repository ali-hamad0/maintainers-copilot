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

(To be filled)

## Phase 3 — Dataset + Splits + Training Notebook

(To be filled)

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
