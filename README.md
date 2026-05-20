# Maintainer's Copilot

An authenticated, tool-calling chatbot that helps open-source maintainers triage closed issues. It classifies issues (bug / feature / docs / question) with three models compared on the same test set, extracts entities, summarizes threads, and answers maintainer questions using advanced RAG over the project's docs and resolved issues. It carries memory across conversations and is embeddable as a small React widget into any host app.

**Solo project. 5 days. Week 7 AIE Bootcamp.**

---

## Description

The Maintainer's Copilot combines:

- **Three-model classification**: Fine-tuned transformer, classical ML baseline, and LLM baseline on the same test split with metrics compared in `DECISIONS.md`
- **Advanced RAG**: Parent-child chunking, hybrid sparse+dense retrieval, cross-encoder reranking, query transformation, and metadata filtering over project docs + resolved issues
- **Stateful chatbot**: Single LLM that picks tools (classify, extract entities, summarize, RAG search, write memory)
- **Persistent memory**: Short-term in Redis, long-term in pgvector with audit logging
- **Two frontends**: Streamlit admin UI + embeddable React widget with origin allowlisting
- **Production-ready observability**: Structured logging with redaction, end-to-end tracing, and CI gates on regression

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Frontend Layer                                              │
├─────────────────────────────────────────────────────────────┤
│  Streamlit (localhost:8501)  │  React Widget (localhost:3000)  │
│  └─ Login                     │  └─ Embed in any host           │
│  └─ Chat                      │  └─ Config-driven theming       │
│  └─ Memory Inspector          │  └─ Widget config (DB-driven)   │
│  └─ Widget Config             │  └─ Origin allowlist check      │
└──────────────┬────────────────────────────────┬──────────────┘
               │                                │
┌──────────────┴────────────────────────────────┴──────────────┐
│ API Layer (FastAPI, localhost:8000)                          │
├──────────────────────────────────────────────────────────────┤
│  ✓ HTTP only. Routers. NO SQLAlchemy, NO Redis, NO HTTP     │
└──────────────┬────────────────────────────────┬──────────────┘
               │                                │
┌──────────────┴────────────────┐   ┌──────────┴──────────────┐
│ Services (Business Logic)      │   │ ModelServer (Inference)│
├────────────────────────────────┤   ├──────────────────────┤
│ ✓ Agent orchestration          │   │ ✓ Classifier         │
│ ✓ RAG pipeline                 │   │ ✓ NER                │
│ ✓ Memory (short & long-term)   │   │ ✓ Summarizer         │
│ ✓ Auth + user management       │   └──────────────────────┘
│ ✓ Widget config CRUD           │
└────────────────┬────────────────┘
                 │
┌────────────────┴────────────────────────────────────────────┐
│ Data Layer (Repositories, SQL Only)                          │
├──────────────────────────────────────────────────────────────┤
│ ✓ User queries, memory queries, widget config, conversations│
└────────────────┬────────────────────────────────────────────┘
                 │
        ┌────────┴─────────┬────────────┬──────────────┐
        │                  │            │              │
   ┌────▼─────┐   ┌───────▼──┐  ┌─────▼───┐  ┌─────▼──┐
   │ PostgreSQL│   │  Redis   │  │  MinIO  │  │  Vault │
   │ + pgvector│   │ (session)│  │(artifacts)│ │(secrets)
   └──────────┘   └──────────┘  └─────────┘  └────────┘
```

**Layer Boundaries:**
- `api/`: HTTP routers only. Returns DTOs. Depends on `services` + `domain`.
- `services/`: Business logic, transactions, memory invalidation. Depends on `repositories`, `domain`, `infra`.
- `repositories/`: SQL only. No HTTP, no cache logic. Returns ORM models.
- `domain/`: Pydantic models (distinct from SQLAlchemy ORM).
- `infra/`: Adapters for Vault, MinIO, Redis, LLM, tracing, redaction.

See `ARCH.md` for detailed request-to-row paths and trace trees.

---

## Prerequisites

- **Python 3.12** (check with `python --version`)
- **uv** (install: `pip install uv`)
- **Docker + Docker Compose** (check: `docker --version && docker-compose --version`)
- **Node 20+** (for widget; check: `node --version`)
- **Git** (check: `git --version`)

**Accounts needed (by Phase 3+):**
- GitHub (for dataset)
- OpenAI or Anthropic (for LLM calls)
- LangSmith / Langfuse / Phoenix (for tracing)

---

## Setup

### 1. Clone and Install Dependencies

```bash
git clone <repo-url>
cd maintainers-copilot
cp .env.example .env
```

Edit `.env` and fill in the Vault root token (from bootcamp materials).

### 2. Install Python Environments

Each service has its own locked environment:

```bash
# Backend
cd backend
uv pip install -e .[dev]
cd ..

# ModelServer
cd modelserver
uv pip install -e .[dev]
cd ..

# Chatbot
cd chatbot
uv pip install -e .[dev]
cd ..
```

### 3. Install Pre-commit Hooks

```bash
pip install pre-commit
pre-commit install
```

Verify hooks run on next commit:

```bash
pre-commit run --all-files
```

### 4. Bring Up the Stack

```bash
docker-compose up
```

On first boot:
- Vault initializes in dev mode
- Alembic migrates the schema
- All services boot and healthcheck

Verify all containers are healthy:

```bash
docker-compose ps
```

---

## Run

### Develop Locally

**Backend API** (with hot reload):

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Visit [http://localhost:8000/docs](http://localhost:8000/docs) for Swagger UI.

**Streamlit Chatbot**:

```bash
cd chatbot
streamlit run app.py
```

Visit [http://localhost:8501](http://localhost:8501).

**ModelServer** (inference):

```bash
cd modelserver
uvicorn app.main:app --reload --port 8001
```

**Widget** (React):

```bash
cd widget
npm install
npm run dev
```

Visit [http://localhost:5173](http://localhost:5173) (Vite dev server).

### Run Tests

```bash
cd backend
pytest -v
```

### Run Linting + Type Checking

```bash
cd backend
ruff check .
black --check .
mypy .
```

Or via pre-commit:

```bash
pre-commit run --all-files
```

---

## Environment Variables

All runtime config comes from `.env`. Critical ones:

| Variable | Purpose | Example |
|----------|---------|---------|
| `VAULT_ROOT_TOKEN` | Access secrets | `s.xxxxxxxxxxxxxxxx` |
| `DATABASE_URL` | PostgreSQL async connection | `postgresql+asyncpg://user:pass@db:5432/db` |
| `REDIS_URL` | Short-term memory | `redis://redis:6379/0` |
| `MINIO_URL` | Blob storage (models, evals) | `http://minio:9000` |
| `API_PORT` / `CHATBOT_PORT` | Service ports | `8000` / `8501` |
| `ENVIRONMENT` | dev / prod | `development` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |

See `.env.example` for the full list. Every variable has a sane default or a clear placeholder.

---

## Project Structure

```
maintainers-copilot/
├── backend/                     # FastAPI backend service
│   ├── app/
│   │   ├── api/                 # HTTP routers (no logic)
│   │   ├── services/            # Business logic, transactions
│   │   ├── repositories/        # SQL only
│   │   ├── domain/              # Pydantic models
│   │   ├── infra/               # Vault, MinIO, Redis, LLM, tracing, redaction
│   │   ├── config.py            # Settings class (extra="forbid")
│   │   ├── dependencies.py       # Dependency injection
│   │   └── main.py              # FastAPI app, lifespan
│   ├── alembic/                 # Database migrations
│   ├── tests/                   # Unit + integration tests
│   ├── pyproject.toml           # Backend dependencies (pinned)
│   ├── Dockerfile
│   └── .dockerignore
├── chatbot/                     # Streamlit admin UI
│   ├── app.py                   # Entrypoint
│   ├── pages/                   # Streamlit pages (login, chat, memory, config)
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── .dockerignore
├── modelserver/                 # FastAPI inference (classifier, NER, summarizer)
│   ├── app/
│   │   ├── api/
│   │   ├── services/
│   │   ├── infra/
│   │   └── main.py
│   ├── tests/
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── .dockerignore
├── widget/                      # React + Vite bundle
│   ├── src/
│   │   ├── App.tsx
│   │   ├── components/
│   │   └── index.tsx
│   ├── public/widget.js         # Loader script for embed
│   ├── package.json
│   ├── vite.config.ts
│   ├── Dockerfile
│   └── .dockerignore
├── host/                        # Demo host app (nginx)
│   ├── public/index.html        # Page embedding widget
│   ├── nginx.conf
│   ├── Dockerfile
│   └── .dockerignore
├── prompts/                     # Version-controlled prompt files
│   ├── system_chat.md           # Main system prompt
│   ├── rag_answer.md            # RAG-specific prompt
│   ├── no_context.md            # Fallback when no context
│   └── memory_decision.md        # When to write memory
├── evals/                       # Golden sets + harness
│   ├── golden/
│   │   ├── classification.jsonl # 25 hand-curated classification examples
│   │   └── rag.jsonl            # 25 Q&A triples
│   ├── artefacts/               # Confusion matrices, plots
│   └── run_classification_eval.py
├── notebooks/                   # Jupyter notebooks (colab-ready)
│   ├── train_classifier.ipynb
│   └── train_classical_baseline.ipynb
├── docs/
│   └── _references/             # Study material (gitignored)
├── .env.example                 # Environment template
├── .gitignore
├── .gitattributes
├── .pre-commit-config.yaml
├── docker-compose.yml           # All 10 services
├── CLAUDE.md                    # This contract
├── ARCH.md                      # Architecture + request paths
├── DECISIONS.md                 # Every numbered decision
├── RUNBOOK.md                   # Ops & debugging
├── EVALS.md                     # Golden sets, metrics, judge, thresholds
├── SECURITY.md                  # Redaction patterns, threat model
└── README.md                    # You are here
```

**Key rules:**
- No `utils.py`, `helpers.py`, `stage1.py`, `misc.py`. File names describe what the file does.
- Each top-level service (`backend/`, `chatbot/`, `widget/`, `modelserver/`) has its own `pyproject.toml`/`package.json` and Dockerfile.
- No shared `__init__.py` across service boundaries.

---

## Deployment

### Docker Compose (Development)

```bash
docker-compose up -d
```

All 10 services come up together:
- `api`, `chatbot`, `widget`, `modelserver`, `host` (app services)
- `db`, `redis`, `minio`, `vault` (data/infrastructure)
- `migrate` (one-shot DB setup, then exits)

Health checks pass before dependent services start.

### Docker Compose (Production-ish)

For a closer-to-production setup:

```bash
docker-compose -f docker-compose.prod.yml up -d
```

(To be created in later phases.)

### Fresh Clone Test

From a clean machine:

```bash
git clone <repo>
cd maintainers-copilot
cp .env.example .env
# Fill VAULT_ROOT_TOKEN
docker-compose up
curl http://localhost:8000/healthz  # Should return 200
```

---

## Submission Block

```
Project 7 — Ali Hamad
Repo: https://github.com/ali-hamad0/maintainers-copilot
Tag: v0.1.0-week7
Dataset: pandas-dev/pandas issues, 1373 train / 262 val / 327 test (time-based split)
Classification — Classical (TF-IDF+LR): F1=0.8804 | Fine-tuned (DistilBERT): F1=0.6483 | LLM (Gemini 2.5 Flash): F1=0.6888
Deployment choice: DistilBERT — no API dependency; 122ms latency acceptable for async triage
Embedding model: gemini-embedding-001 — API consistency with Gemini LLM; 768-dim locked at Phase 1
RAG — hit@5=0.80 | MRR@10=0.531 | Faithfulness=pending live judge | Answer relevancy=pending live judge
Long-term memory type: episodic
Tracing backend: LangSmith — bootcamp default; native Google GenAI tracing via langsmith SDK
Widget bundle size: 48 KB (gzipped)
LLM: Google Gemini 2.5 Flash (gemini-2.5-flash)
```

---

## References

See `docs/_references/` (study material, gitignored):

- `AIE_Week7_Maintainers_Copilot_v4.txt` — The brief and grade rubric
- `AIE_Bootcamp_Coding_Guidelines.txt` — Style, security, testing
- `Engineering_Standards_Companion_Guide.txt` — Async, DI, lifespan, Settings
- `code_review_guidelines.txt` — Defend-every-line standard
- `week3_code_review_lessons_learned.txt` — RAG, OOP
- `week4_code_review_lessons_learned.txt` — Auth, persistence, agents, tracing
- `Advanced_RAG_Techniques_Guide.txt` — Eight RAG techniques
- `guides.txt` — Casaba Agentic AI Security Spec

---

## License

MIT

---

*Last updated: Phase 1 (skeleton). See DECISIONS.md for per-phase choices. See ARCH.md for architecture. See RUNBOOK.md for operations.*
