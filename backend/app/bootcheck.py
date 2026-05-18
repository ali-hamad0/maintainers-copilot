"""Startup assertions (CLAUDE.md §4).

Called at the very beginning of lifespan() before any service is started.
Any failure here aborts boot with a clear error message.
"""

from pathlib import Path

import httpx
import structlog
import yaml

log = structlog.get_logger(__name__)

_THRESHOLDS_FILE = Path(__file__).parent.parent / "eval_thresholds.yaml"
_REQUIRED_VAULT_PATHS = ("jwt", "db", "minio", "tracing", "gemini")


async def assert_vault_reachable_and_seeded(vault_addr: str, vault_token: str) -> None:
    """Refuse to boot if Vault is unreachable or any required secret path is missing."""
    try:
        async with httpx.AsyncClient() as client:
            health_resp = await client.get(
                f"{vault_addr}/v1/sys/health",
                headers={"X-Vault-Token": vault_token},
                timeout=5.0,
            )
            if health_resp.status_code not in (200, 429, 473):
                raise RuntimeError(
                    f"Vault health check returned {health_resp.status_code} — "
                    "is Vault unsealed and reachable?"
                )

            for path in _REQUIRED_VAULT_PATHS:
                resp = await client.get(
                    f"{vault_addr}/v1/secret/data/{path}",
                    headers={"X-Vault-Token": vault_token},
                    timeout=5.0,
                )
                if resp.status_code == 404:
                    raise RuntimeError(
                        f"Required Vault secret '{path}' not found — "
                        "run scripts/vault-bootstrap.sh first"
                    )
                resp.raise_for_status()

    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Cannot connect to Vault at {vault_addr}: {exc}\n"
            "Ensure the 'vault' service is running before starting 'api'."
        ) from exc

    log.info("bootcheck.vault_ok", addr=vault_addr)


def assert_eval_thresholds_valid() -> None:
    """Refuse to boot if eval_thresholds.yaml has any threshold set to 0 or 'disabled'."""
    if not _THRESHOLDS_FILE.exists():
        raise RuntimeError(
            f"eval_thresholds.yaml not found at {_THRESHOLDS_FILE} — "
            "this file must be committed and contain non-zero thresholds"
        )

    with _THRESHOLDS_FILE.open() as fh:
        thresholds: dict[str, object] = yaml.safe_load(fh) or {}

    def _check(mapping: dict[str, object], prefix: str = "") -> None:
        for key, value in mapping.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                _check(value, full_key)
            elif value in (0, 0.0, "disabled", False, None):
                raise RuntimeError(
                    f"eval_thresholds.yaml: '{full_key}' is set to {value!r} — "
                    "all thresholds must be non-zero and not 'disabled'"
                )

    _check(thresholds)
    log.info("bootcheck.eval_thresholds_ok", file=str(_THRESHOLDS_FILE))


async def run_all(vault_addr: str, vault_token: str) -> None:
    """Run every startup assertion. Called once in lifespan()."""
    assert_eval_thresholds_valid()
    await assert_vault_reachable_and_seeded(vault_addr, vault_token)
