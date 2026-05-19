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


async def assert_modelserver_healthy(modelserver_url: str, expected_sha256: str) -> None:
    """Refuse to boot if modelserver is down, weights missing, or SHA-256 mismatches.

    CLAUDE.md §4 #2: classifier weights file is missing.
    CLAUDE.md §4 #3: weights SHA-256 does not match the model card.
    """
    healthz_url = modelserver_url.rstrip("/") + "/healthz"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(healthz_url, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Cannot connect to modelserver at {modelserver_url}: {exc}\n"
            "Ensure the 'modelserver' service is running before starting 'api'."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"Modelserver healthz returned {exc.response.status_code} — "
            "modelserver may still be booting."
        ) from exc

    if not data.get("model_loaded"):
        raise RuntimeError(
            "Modelserver reports classifier weights are NOT loaded.\n"
            "Check modelserver logs — weights may be missing from MinIO."
        )

    actual_sha256 = data.get("weights_sha256", "")
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise RuntimeError(
            f"Classifier weights SHA-256 mismatch!\n"
            f"  Expected (model_card.md) : {expected_sha256}\n"
            f"  Reported by modelserver  : {actual_sha256}\n"
            "Re-upload the correct weights or update CLASSIFIER_WEIGHTS_SHA256."
        )

    log.info(
        "bootcheck.modelserver_ok",
        url=modelserver_url,
        sha256=actual_sha256 or "not-verified",
    )


async def run_all(
    vault_addr: str,
    vault_token: str,
    modelserver_url: str,
    classifier_weights_sha256: str,
) -> None:
    """Run every startup assertion. Called once in lifespan()."""
    assert_eval_thresholds_valid()
    await assert_vault_reachable_and_seeded(vault_addr, vault_token)
    await assert_modelserver_healthy(modelserver_url, classifier_weights_sha256)
