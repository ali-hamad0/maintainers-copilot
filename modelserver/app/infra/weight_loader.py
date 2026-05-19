"""Download classifier weights from MinIO, verify SHA-256, return local path.

Called once during lifespan startup.  Raises RuntimeError on any failure so
the modelserver refuses to boot with a clear message.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import structlog
from minio import Minio
from minio.error import S3Error

log = structlog.get_logger(__name__)

_WEIGHTS_CACHE: Path | None = None


def _minio_client(endpoint: str, access_key: str, secret_key: str) -> Minio:
    secure = endpoint.startswith("https://")
    stripped = endpoint.removeprefix("http://").removeprefix("https://")
    return Minio(stripped, access_key=access_key, secret_key=secret_key, secure=secure)


def download_and_verify(
    *,
    minio_endpoint: str,
    minio_access_key: str,
    minio_secret_key: str,
    minio_bucket: str,
    minio_key: str,
    expected_sha256: str,
) -> Path:
    """Return path to verified weights file (cached after first call).

    Raises RuntimeError if:
    - The object does not exist in MinIO.
    - The downloaded file's SHA-256 does not match *expected_sha256*
      (only checked when *expected_sha256* is non-empty).
    """
    global _WEIGHTS_CACHE
    if _WEIGHTS_CACHE is not None and _WEIGHTS_CACHE.exists():
        return _WEIGHTS_CACHE

    client = _minio_client(minio_endpoint, minio_access_key, minio_secret_key)

    try:
        client.stat_object(minio_bucket, minio_key)
    except S3Error as exc:
        if exc.code in ("NoSuchKey", "NoSuchBucket"):
            raise RuntimeError(
                f"Classifier weights not found in MinIO: "
                f"bucket='{minio_bucket}' key='{minio_key}'\n"
                "Run the Colab notebook and upload_weights.py first."
            ) from exc
        raise RuntimeError(f"MinIO error while checking weights: {exc}") from exc

    tmp = Path(tempfile.mkstemp(suffix=".pt", prefix="classifier_weights_")[1])
    log.info("weight_loader.downloading", bucket=minio_bucket, key=minio_key, dest=str(tmp))

    client.fget_object(minio_bucket, minio_key, str(tmp))

    actual_sha256 = hashlib.sha256(tmp.read_bytes()).hexdigest()
    log.info("weight_loader.downloaded", sha256=actual_sha256, size_mb=tmp.stat().st_size / 1e6)

    if expected_sha256 and actual_sha256 != expected_sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"Classifier weights SHA-256 mismatch!\n"
            f"  Expected : {expected_sha256}\n"
            f"  Got      : {actual_sha256}\n"
            "Update WEIGHTS_SHA256 in your env or re-upload the correct weights."
        )

    _WEIGHTS_CACHE = tmp
    return tmp
