#!/bin/sh
# Vault bootstrap: writes placeholder secrets on first boot.
# Re-running is idempotent — kv put overwrites safely.
set -e

echo "Waiting for Vault to be ready..."
until vault status > /dev/null 2>&1; do
  sleep 1
done

echo "Enabling KV v2 secrets engine (idempotent)..."
vault secrets enable -version=2 -path=secret kv 2>/dev/null || true

echo "Writing placeholder secrets..."

# JWT signing key — replace with a strong random value in production
vault kv put secret/jwt \
  secret="CHANGE_ME_strong_jwt_signing_key_at_least_32_chars"

# Google Gemini API key — set real key via:
#   docker exec <vault-container> vault kv put secret/gemini api_key="AIza..."
vault kv put secret/gemini \
  api_key="AIza-placeholder-gemini-key"

# Database credentials — must match POSTGRES_PASSWORD in docker-compose
vault kv put secret/db \
  password="postgres"

# MinIO credentials — must match MINIO_ROOT_USER/PASSWORD in docker-compose
vault kv put secret/minio \
  access_key="minioadmin" \
  secret_key="minioadmin123"

# Tracing backend (LangSmith) API key
#   Replace with real key: docker exec <vault> vault kv put secret/tracing api_key="ls_..."
vault kv put secret/tracing \
  api_key="ls-placeholder-langsmith-key"

echo "Vault bootstrap complete."
