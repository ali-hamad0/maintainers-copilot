# RUNBOOK.md — Operations

## Bring the Stack Up

### Prerequisites
- Docker Desktop running
- `git`, `docker compose` v2 available
- Gemini API key (get one free at aistudio.google.com)
- LangSmith API key (optional — tracing degrades gracefully without it)

### First boot (fresh clone)

```bash
git clone https://github.com/ali-hamad0/maintainers-copilot.git
cd maintainers-copilot
cp .env.example .env
```

The default `VAULT_ROOT_TOKEN=dev-root-token` in `.env` is fine for local dev.

```bash
docker compose up --build -d
```

Boot order is enforced by health checks:
1. `db`, `redis`, `minio`, `vault` start and become healthy.
2. `vault-bootstrap` writes placeholder secrets to Vault and exits.
3. `migrate` runs `alembic upgrade head` and exits 0.
4. `api`, `modelserver`, `chatbot`, `widget`, `host`, `host-blocked` start.

Verify everything is healthy:

```bash
docker compose ps
curl http://localhost:8000/healthz   # {"status":"ok"}
```

### Set real secrets (required for LLM calls and tracing)

After the stack is up, inject real secrets into Vault:

```bash
# Gemini API key (required for classify, summarise, RAG, agent)
docker exec maintainers-copilot-vault-1 \
  vault kv put secret/gemini api_key="AIza_your_real_key"

# LangSmith API key (optional — tracing is a no-op without it)
docker exec maintainers-copilot-vault-1 \
  vault kv put secret/tracing api_key="ls-your-langsmith-key"

# JWT signing key (change from placeholder before production)
docker exec maintainers-copilot-vault-1 \
  vault kv put secret/jwt secret="your-strong-random-32-char-key"
```

Restart the `api` service so it picks up the new secrets:

```bash
docker compose restart api
```

### Subsequent boots (volumes still present)

```bash
docker compose up -d
```

The `vault-bootstrap` service is idempotent — it skips any secret that already has a non-placeholder value.

---

## Ingest Corpus

The RAG corpus is sourced from `pandas-dev/pandas` closed issues. Ingestion embeds each issue using `gemini-embedding-001` and writes parent + child chunks to pgvector.

### Prerequisites
- `api` service running and healthy
- Real Gemini API key set in Vault (see above)
- `uv` installed locally (`pip install uv`)

### Steps

```bash
cd backend

# 1. Fetch issues from GitHub (requires GITHUB_TOKEN in .env)
uv run python scripts/fetch_issues.py --repo pandas-dev/pandas --output data/issues_raw.json

# 2. Map labels, split by time, write JSONL splits
uv run python scripts/split_issues.py \
  --input data/issues_raw.json \
  --output-dir data/splits

# 3. (Optional) Inject synthetic question examples
uv run python scripts/inject_questions.py \
  --splits-dir data/splits

# 4. Ingest the RAG corpus (rag.jsonl split) into pgvector
uv run python scripts/ingest_corpus.py \
  --splits-dir data/splits \
  --api-base http://localhost:8000

# 5. Download pre-split data (if skipping fetch)
uv run python scripts/download_splits.py \
  --output-dir data/splits
```

Expected output from `ingest_corpus.py`:
```
Ingesting 218 issues...
Created 218 parents, 1744 children (8 avg children/issue)
HNSW index built on 1744 vectors (dim=768)
```

---

## Retrain the Model

The fine-tuned DistilBERT classifier is trained in a Colab notebook.  
The TF-IDF+LR pipeline is retrained inline by the CI eval harness.

### DistilBERT (Colab)

1. Open `notebooks/train_classifier.ipynb` in Google Colab (T4 runtime).
2. Set the GitHub token cell (to download the training JSONL from MinIO or the splits directory).
3. Run all cells. Training takes ~15–20 min on a T4.
4. The notebook saves `weights.pt` locally in Colab.
5. Upload the weights and record the SHA-256:

```bash
# From backend/ with MinIO running
uv run python scripts/upload_weights.py \
  --weights /path/to/weights.pt \
  --minio-endpoint localhost:9000
```

`upload_weights.py` prints the SHA-256. Update `WEIGHTS_SHA256` in `.env` and `model_card.md`.

6. Restart modelserver so it downloads and verifies the new weights:

```bash
docker compose restart modelserver
```

### TF-IDF+LR (local)

```bash
cd backend
uv run python scripts/eval_classical_baseline.py \
  --splits-dir data/splits
```

This retrains on the full train split (1373 examples), evaluates on the test split, and uploads the pipeline to MinIO.

---

## Seed Vault

On a fresh Vault instance, `vault-bootstrap` (compose service) writes all placeholders automatically.  
To set real secrets, use the commands in [Set real secrets](#set-real-secrets-required-for-llm-calls-and-tracing) above.

### Manual bootstrap (if vault-bootstrap is not available)

```bash
# Enter the Vault container
docker exec -it maintainers-copilot-vault-1 sh

# Inside the container:
vault secrets enable -version=2 -path=secret kv

vault kv put secret/jwt       secret="CHANGE_ME_strong_jwt_signing_key_at_least_32_chars"
vault kv put secret/gemini    api_key="AIza-placeholder-gemini-key"
vault kv put secret/db        password="postgres"
vault kv put secret/minio     access_key="minioadmin" secret_key="minioadmin123"
vault kv put secret/tracing   api_key="ls-placeholder-langsmith-key"
```

### Verify secrets are present

```bash
docker exec maintainers-copilot-vault-1 vault kv list secret
# Expected:
# Keys
# ----
# db
# gemini
# jwt
# minio
# tracing
```

---

## Read Traces

Traces require a real LangSmith API key (see above). Once set:

1. Open [smith.langchain.com](https://smith.langchain.com) and log in.
2. Navigate to the **maintainers-copilot** project.
3. Each user message is a root trace named `chat.turn`.
4. Click a trace to expand the tree:
   - `llm.call` — Gemini `generateContent` call (model, token counts, latency_ms)
   - `tool.classify` — modelserver `/v1/classify` HTTP call
   - `tool.retrieve` — hybrid BM25 + dense + RRF + rerank pipeline
   - `tool.write_memory` — memory write (payload after redaction)
5. To join a trace to its logs: copy the `trace_id` from the span and search in logs:
   ```bash
   docker compose logs api | grep <trace_id>
   ```
   Every structlog line for that request carries the same `trace_id`.

### Demo the error path

Trigger a ToolError by calling classify with an empty string:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"message": "classify this: ", "conversation_id": "test-error"}' \
  --no-buffer
```

The resulting trace will show `tool.classify` → `error` span with `retryable=false`.

---

## Wipe Everything and Start Over

```bash
# Stop all containers
docker compose down

# Remove ALL volumes (database, redis, minio, vault data)
docker compose down -v

# Remove built images (optional — forces fresh image pull/rebuild)
docker compose down --rmi local

# Start fresh
docker compose up --build -d
```

To wipe only the database (keep MinIO weights and Vault secrets):

```bash
docker compose stop db migrate api chatbot
docker volume rm maintainers-copilot_db_data
docker compose up -d
```

---

## Common Errors & Fixes

### `api` refuses to start: "Vault unreachable"

**Cause:** The `vault` container is not healthy, or `VAULT_ADDR` is wrong.

```bash
docker compose ps vault
# If not healthy:
docker compose logs vault
docker compose restart vault
docker compose restart vault-bootstrap
docker compose restart api
```

### `api` refuses to start: "Classifier weights file missing"

**Cause:** `WEIGHTS_SHA256` in `.env` is set but the modelserver cannot find the file.

```bash
# Check modelserver logs
docker compose logs modelserver | grep weights
# If weights not downloaded:
docker exec maintainers-copilot-modelserver-1 ls /app/weights/
```

If the weights file is missing, run `upload_weights.py` first (see [Retrain the Model](#retrain-the-model)), then restart modelserver.

To skip the SHA-256 gate temporarily (local dev only):

```bash
# In .env:
WEIGHTS_SHA256=
docker compose restart api
```

### `migrate` exits non-zero: "alembic.util.exc.CommandError"

**Cause:** The database schema has diverged or a migration is broken.

```bash
docker compose logs migrate
# Check current revision:
docker exec maintainers-copilot-db-1 psql -U postgres -d maintainers_copilot \
  -c "SELECT version_num FROM alembic_version;"
# Inspect migration history:
docker compose run --rm migrate alembic history
```

To reset the schema (destroys all data):

```bash
docker compose down -v
docker compose up -d
```

### Widget is blocked on `localhost:8080`

**Cause:** The widget config in Postgres does not include `http://localhost:8080` in `allowed_origins`.

Fix via Streamlit admin UI (http://localhost:8501):
1. Log in with admin credentials.
2. Go to **Widget Config** page.
3. Create or edit a widget config, add `http://localhost:8080` to `Allowed Origins`.
4. Wait up to 30 seconds (CORS cache TTL) or restart the `api` service.

Or via the API directly:
```bash
curl -X POST http://localhost:8000/widgets \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "demo", "allowed_origins": ["http://localhost:8080"]}'
```

### `POST /chat` returns `{"detail": "Unauthorized"}` (401)

**Cause:** JWT token is missing, expired, or malformed.

```bash
# Get a fresh token:
curl -X POST http://localhost:8000/auth/jwt/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=user@example.com&password=yourpassword"
```

Access tokens expire after 60 minutes. Re-login to get a new token.

### `POST /auth/register` returns `400 REGISTER_USER_ALREADY_EXISTS`

The email is already registered. Use the login endpoint instead.

### Gemini calls fail with `PERMISSION_DENIED`

**Cause:** The Gemini API key in Vault is the placeholder value.

```bash
docker exec maintainers-copilot-vault-1 \
  vault kv get -field=api_key secret/gemini
# If it prints "AIza-placeholder-gemini-key":
docker exec maintainers-copilot-vault-1 \
  vault kv put secret/gemini api_key="YOUR_REAL_KEY"
docker compose restart api
```

### LangSmith tracing shows no spans

**Cause:** Either the tracing API key is the placeholder, or langsmith is in no-op mode.

```bash
docker compose logs api | grep "tracing"
# Look for: "tracing.startup span emitted" vs "tracing disabled"
```

If disabled, set the real LangSmith API key:
```bash
docker exec maintainers-copilot-vault-1 \
  vault kv put secret/tracing api_key="ls-your-real-key"
docker compose restart api
```

### CI eval gate fails: "tfidf_lr.f1_macro: X < threshold Y"

**Cause:** Either the model regressed or the threshold was accidentally raised.

```bash
# Run locally to reproduce:
cd backend
uv run python ../evals/run_classification_eval.py \
  --use-ci-fixture --skip-distilbert --skip-gemini --skip-minio-upload
```

Check `eval_thresholds.yaml` — any threshold that was manually increased without a corresponding model improvement will fail. Fix by reverting the threshold change or retraining.
