"""Async Vault KV v2 client.

Reads secrets at startup and exposes them as a typed dataclass attached to
app.state.  All secret access in the application goes through app.state.secrets.
Never call os.getenv for secrets — read from this module.
"""

import dataclasses
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_REQUIRED_PATHS = ("jwt", "db", "minio", "tracing", "gemini")


@dataclasses.dataclass
class VaultSecrets:
    jwt_secret: str
    db_password: str
    minio_access_key: str
    minio_secret_key: str
    langsmith_api_key: str
    gemini_api_key: str
    openai_api_key: str
    anthropic_api_key: str


async def _read_kv(
    client: httpx.AsyncClient, vault_addr: str, token: str, path: str
) -> dict[str, Any]:
    url = f"{vault_addr}/v1/secret/data/{path}"
    resp = await client.get(url, headers={"X-Vault-Token": token}, timeout=5.0)
    resp.raise_for_status()
    body: dict[str, Any] = resp.json()
    data: dict[str, Any] = body["data"]["data"]
    return data


async def load_secrets(vault_addr: str, vault_token: str) -> VaultSecrets:
    """Read all required secret paths from Vault KV v2 and return a typed bundle."""
    async with httpx.AsyncClient() as client:
        jwt_data = await _read_kv(client, vault_addr, vault_token, "jwt")
        db_data = await _read_kv(client, vault_addr, vault_token, "db")
        minio_data = await _read_kv(client, vault_addr, vault_token, "minio")
        tracing_data = await _read_kv(client, vault_addr, vault_token, "tracing")
        gemini_data = await _read_kv(client, vault_addr, vault_token, "gemini")

        # openai and anthropic are optional placeholders; don't fail if missing
        try:
            openai_data = await _read_kv(client, vault_addr, vault_token, "openai")
            openai_key: str = openai_data.get("api_key", "")
        except Exception:
            openai_key = ""

        try:
            anthropic_data = await _read_kv(client, vault_addr, vault_token, "anthropic")
            anthropic_key: str = anthropic_data.get("api_key", "")
        except Exception:
            anthropic_key = ""

    secrets = VaultSecrets(
        jwt_secret=jwt_data["secret"],
        db_password=db_data["password"],
        minio_access_key=minio_data["access_key"],
        minio_secret_key=minio_data["secret_key"],
        langsmith_api_key=tracing_data["api_key"],
        gemini_api_key=gemini_data["api_key"],
        openai_api_key=openai_key,
        anthropic_api_key=anthropic_key,
    )
    log.info("vault.secrets_loaded", paths=list(_REQUIRED_PATHS))
    return secrets


async def assert_vault_reachable(vault_addr: str, vault_token: str) -> None:
    """Raise RuntimeError if Vault is unreachable or the token is invalid."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{vault_addr}/v1/sys/health",
                headers={"X-Vault-Token": vault_token},
                timeout=5.0,
            )
            # 200 = initialized + unsealed + active; 429 = standby (also fine for dev)
            if resp.status_code not in (200, 429, 473):
                raise RuntimeError(
                    f"Vault health check returned unexpected status {resp.status_code}"
                )
    except httpx.ConnectError as exc:
        raise RuntimeError(f"Cannot reach Vault at {vault_addr}: {exc}") from exc
