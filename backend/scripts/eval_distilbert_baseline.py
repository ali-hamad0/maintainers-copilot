#!/usr/bin/env python3
"""Evaluate the fine-tuned DistilBERT classifier on the test split via the modelserver.

No retraining.  The modelserver must be running with the correct weights loaded.
Results are uploaded to MinIO under models/distilbert/<run_id>/eval.json.

Usage
-----
    python backend/scripts/eval_distilbert_baseline.py

    # Point at a non-default modelserver:
    MODELSERVER_URL=http://localhost:8001 python backend/scripts/eval_distilbert_baseline.py

Environment (loaded from .env):
    MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_LOCAL_ENDPOINT, MINIO_BUCKET
    MODELSERVER_URL  (default: http://localhost:8001)
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import numpy as np
from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error
from sklearn.metrics import accuracy_score, classification_report, f1_score

REPO_ROOT = Path(__file__).parent.parent.parent
SPLIT_PREFIX = "splits/v1"
LABELS = ["bug", "feature", "docs", "question"]


def _load_env() -> dict[str, str]:
    load_dotenv(REPO_ROOT / ".env", override=False)
    required = ["MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_LOCAL_ENDPOINT", "MINIO_BUCKET"]
    config: dict[str, str] = {}
    missing: list[str] = []
    for key in required:
        val = os.getenv(key)
        if not val:
            missing.append(key)
        else:
            config[key] = val
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return config


def _minio_client(cfg: dict[str, str]) -> Minio:
    endpoint = cfg["MINIO_LOCAL_ENDPOINT"].removeprefix("http://").removeprefix("https://")
    secure = not (endpoint.startswith("localhost") or endpoint.startswith("127."))
    return Minio(
        endpoint,
        access_key=cfg["MINIO_ACCESS_KEY"],
        secret_key=cfg["MINIO_SECRET_KEY"],
        secure=secure,
    )


def _download_split(client: Minio, bucket: str, split_name: str) -> list[dict]:
    key = f"{SPLIT_PREFIX}/{split_name}.jsonl"
    try:
        response = client.get_object(bucket, key)
        return [
            json.loads(line)
            for line in response.read().decode("utf-8").splitlines()
            if line.strip()
        ]
    except S3Error as exc:
        print(f"ERROR: could not download {key}: {exc}", file=sys.stderr)
        sys.exit(1)


def _classify_batch(
    client: httpx.Client, url: str, texts: list[str]
) -> tuple[list[str], list[float]]:
    """Return (labels, latencies_ms) for each text."""
    predictions: list[str] = []
    latencies: list[float] = []
    for text in texts:
        t0 = time.perf_counter()
        resp = client.post(f"{url}/v1/classify", json={"text": text})
        resp.raise_for_status()
        latencies.append((time.perf_counter() - t0) * 1000)
        predictions.append(resp.json()["label"])
    return predictions, latencies


def main() -> None:
    cfg = _load_env()
    minio = _minio_client(cfg)
    bucket = cfg["MINIO_BUCKET"]
    modelserver_url = os.getenv("MODELSERVER_URL", "http://localhost:8001")

    # Health check
    try:
        with httpx.Client(timeout=5.0) as hc:
            resp = hc.get(f"{modelserver_url}/healthz")
            resp.raise_for_status()
            sha = resp.json().get("weights_sha256", "unknown")
            print(f"Modelserver OK  weights_sha256={sha[:12]}...")
    except Exception as exc:
        print(f"ERROR: modelserver not reachable at {modelserver_url}: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Downloading test split ...")
    test_rows = _download_split(minio, bucket, "test")
    print(f"  {len(test_rows)} test examples")

    texts_test = [r["text"] for r in test_rows]
    y_test = [r["label"] for r in test_rows]

    print("Running DistilBERT inference (single-sample, measures latency) ...")
    with httpx.Client(timeout=60.0) as client:
        y_pred, latencies = _classify_batch(client, modelserver_url, texts_test)

    accuracy = accuracy_score(y_test, y_pred)
    macro_f1 = f1_score(y_test, y_pred, average="macro", labels=LABELS, zero_division=0)
    per_class = f1_score(y_test, y_pred, average=None, labels=LABELS, zero_division=0)
    per_class_dict = dict(zip(LABELS, per_class.tolist(), strict=False))

    p50 = float(np.percentile(latencies, 50))
    p95 = float(np.percentile(latencies, 95))

    print(f"\nTest accuracy  : {accuracy:.4f}")
    print(f"Test macro-F1  : {macro_f1:.4f}")
    for cls, score in per_class_dict.items():
        print(f"  {cls:<10} F1 = {score:.4f}")
    print(f"\nLatency (n={len(latencies)}):")
    print(f"  p50 = {p50:.1f} ms   p95 = {p95:.1f} ms")
    print("\nClassification report:")
    print(classification_report(y_test, y_pred, labels=LABELS, zero_division=0))

    run_id = str(uuid.uuid4())
    results = {
        "run_id": run_id,
        "model": "distilbert-base-uncased (fine-tuned)",
        "modelserver_url": modelserver_url,
        "weights_sha256": sha,
        "test_accuracy": round(accuracy, 4),
        "test_macro_f1": round(macro_f1, 4),
        "per_class_f1": {k: round(v, 4) for k, v in per_class_dict.items()},
        "latency_p50_ms": round(p50, 1),
        "latency_p95_ms": round(p95, 1),
        "n_samples": len(test_rows),
    }

    payload = json.dumps(results, indent=2).encode("utf-8")
    key = f"models/distilbert/{run_id}/eval.json"
    minio.put_object(
        bucket, key, io.BytesIO(payload), length=len(payload), content_type="application/json"
    )
    print(f"\nResults uploaded: {key}")
    print("\n--- JSON results (copy into DECISIONS.md) ---")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
