#!/bin/sh
# Vault bootstrap: writes placeholder secrets on first boot.
# Real secrets (non-placeholder values) are NEVER overwritten.
set -e

echo "Waiting for Vault to be ready..."
until vault status > /dev/null 2>&1; do
  sleep 1
done

echo "Enabling KV v2 secrets engine (idempotent)..."
vault secrets enable -version=2 -path=secret kv 2>/dev/null || true

# Write a secret only if the field is currently a placeholder or missing.
# Usage: write_if_placeholder PATH FIELD PLACEHOLDER_VALUE DEFAULT_VALUE
write_if_placeholder() {
  path="$1" field="$2" placeholder="$3" default_value="$4"
  current=$(vault kv get -field="$field" "$path" 2>/dev/null || echo "NOT_SET")
  if [ "$current" = "NOT_SET" ] || [ "$current" = "$placeholder" ]; then
    vault kv patch "$path" "$field=$default_value" 2>/dev/null || \
      vault kv put "$path" "$field=$default_value"
    echo "  wrote $path[$field] (placeholder)"
  else
    echo "  skipped $path[$field] (real value present)"
  fi
}

echo "Bootstrapping secrets (skipping any real values already set)..."

write_if_placeholder secret/jwt \
  secret \
  "CHANGE_ME_strong_jwt_signing_key_at_least_32_chars" \
  "CHANGE_ME_strong_jwt_signing_key_at_least_32_chars"

write_if_placeholder secret/gemini \
  api_key \
  "AIza-placeholder-gemini-key" \
  "AIza-placeholder-gemini-key"

write_if_placeholder secret/db \
  password \
  "postgres" \
  "postgres"

write_if_placeholder secret/minio \
  access_key \
  "minioadmin" \
  "minioadmin"

write_if_placeholder secret/minio \
  secret_key \
  "minioadmin123" \
  "minioadmin123"

write_if_placeholder secret/tracing \
  api_key \
  "ls-placeholder-langsmith-key" \
  "ls-placeholder-langsmith-key"

echo "Vault bootstrap complete."
