#!/usr/bin/env python3
"""Upload weights.pt from local cache to MinIO after Colab training.

Usage
-----
    python backend/scripts/upload_weights.py

Expected input file: backend/weights/weights.pt
  (download this from MyDrive/maintainers-copilot/models/weights.pt after Colab finishes)

Environment (loaded from .env via python-dotenv):
    MINIO_ACCESS_KEY        MinIO root user
    MINIO_SECRET_KEY        MinIO root password
    MINIO_LOCAL_ENDPOINT    host:port of MinIO (e.g. localhost:9000)
    MINIO_BUCKET            Bucket name (e.g. maintainers-copilot)
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from minio import Minio

REPO_ROOT = Path(__file__).parent.parent.parent
WEIGHTS_PATH = Path(__file__).parent.parent / "weights" / "weights.pt"
MINIO_KEY = "models/classifier/weights.pt"


def main() -> None:
    load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)

    required = ["MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_LOCAL_ENDPOINT", "MINIO_BUCKET"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    if not WEIGHTS_PATH.exists():
        print(
            f"ERROR: {WEIGHTS_PATH} not found.\n"
            "Download weights.pt from Google Drive and place it at backend/weights/weights.pt",
            file=sys.stderr,
        )
        sys.exit(1)

    weights_bytes = WEIGHTS_PATH.read_bytes()
    weights_sha256 = hashlib.sha256(weights_bytes).hexdigest()
    print(f"SHA-256: {weights_sha256}")
    print(f"Size   : {len(weights_bytes) / 1e6:.1f} MB")

    endpoint = os.environ["MINIO_LOCAL_ENDPOINT"].removeprefix("http://").removeprefix("https://")
    secure = not (endpoint.startswith("localhost") or endpoint.startswith("127."))
    client = Minio(
        endpoint,
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=secure,
    )

    client.fput_object(
        os.environ["MINIO_BUCKET"],
        MINIO_KEY,
        str(WEIGHTS_PATH),
        content_type="application/octet-stream",
    )
    print(f"Uploaded → {MINIO_KEY}")
    print(f"\nCopy this SHA-256 into model_card.md → Weights SHA-256 field:\n  {weights_sha256}")


if __name__ == "__main__":
    main()
