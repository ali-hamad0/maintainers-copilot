#!/usr/bin/env python3
"""Train TF-IDF + LogisticRegression on train split, evaluate on test split.

Pipeline pickled to MinIO under models/classical/<run_id>/pipeline.pkl.

Usage
-----
    python backend/scripts/eval_classical_baseline.py

Environment (loaded from .env):
    MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_LOCAL_ENDPOINT, MINIO_BUCKET
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import pickle
import sys
import time
import uuid
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.pipeline import Pipeline

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
        rows = [
            json.loads(line)
            for line in response.read().decode("utf-8").splitlines()
            if line.strip()
        ]
        return rows
    except S3Error as exc:
        print(f"ERROR: could not download {key}: {exc}", file=sys.stderr)
        sys.exit(1)


def _measure_latency(
    pipeline: Pipeline, samples: list[str], n_warmup: int = 5, n_measure: int = 200
) -> tuple[float, float]:
    """Return (p50_ms, p95_ms) over single-sample predictions."""
    # warm up
    for text in samples[:n_warmup]:
        pipeline.predict([text])

    latencies: list[float] = []
    measure_samples = (
        samples[:n_measure]
        if len(samples) >= n_measure
        else samples * (n_measure // len(samples) + 1)
    )
    measure_samples = measure_samples[:n_measure]

    for text in measure_samples:
        t0 = time.perf_counter()
        pipeline.predict([text])
        latencies.append((time.perf_counter() - t0) * 1000)

    return float(np.percentile(latencies, 50)), float(np.percentile(latencies, 95))


def main() -> None:
    cfg = _load_env()
    client = _minio_client(cfg)
    bucket = cfg["MINIO_BUCKET"]

    print("Downloading train split …")
    train_rows = _download_split(client, bucket, "train")
    print(f"  {len(train_rows)} train examples")

    print("Downloading test split …")
    test_rows = _download_split(client, bucket, "test")
    print(f"  {len(test_rows)} test examples")

    texts_train = [r["text"] for r in train_rows]
    y_train = [r["label"] for r in train_rows]
    texts_test = [r["text"] for r in test_rows]
    y_test = [r["label"] for r in test_rows]

    pipeline = Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    max_features=50_000,
                    sublinear_tf=True,
                    min_df=2,
                ),
            ),
            (
                "clf",
                LogisticRegression(
                    C=1.0,
                    max_iter=1000,
                    class_weight="balanced",
                    solver="lbfgs",
                    multi_class="multinomial",
                    random_state=42,
                ),
            ),
        ]
    )

    print("\nTraining TF-IDF + LogisticRegression …")
    t_start = time.perf_counter()
    pipeline.fit(texts_train, y_train)
    train_secs = time.perf_counter() - t_start
    print(f"  Training time: {train_secs:.1f}s")

    y_pred = pipeline.predict(texts_test)

    accuracy = accuracy_score(y_test, y_pred)
    macro_f1 = f1_score(y_test, y_pred, average="macro")
    per_class = f1_score(y_test, y_pred, average=None, labels=LABELS)
    per_class_dict = dict(zip(LABELS, per_class.tolist(), strict=False))

    print(f"\nTest accuracy : {accuracy:.4f}")
    print(f"Test macro-F1 : {macro_f1:.4f}")
    for cls, score in per_class_dict.items():
        print(f"  {cls:<10} F1 = {score:.4f}")

    print("\nClassification report:")
    print(classification_report(y_test, y_pred, labels=LABELS))

    print("Measuring latency (single-sample predictions) …")
    p50, p95 = _measure_latency(pipeline, texts_test)
    print(f"  p50 = {p50:.3f} ms   p95 = {p95:.3f} ms")

    run_id = str(uuid.uuid4())
    buf = io.BytesIO()
    pickle.dump(pipeline, buf)
    buf.seek(0)
    payload = buf.read()
    sha256 = hashlib.sha256(payload).hexdigest()

    key = f"models/classical/{run_id}/pipeline.pkl"
    buf.seek(0)
    client.put_object(
        bucket, key, buf, length=len(payload), content_type="application/octet-stream"
    )
    print(f"\nPipeline uploaded: {key}")
    print(f"  SHA-256 : {sha256}")
    print(f"  Size    : {len(payload) / 1024:.1f} KB")

    results = {
        "run_id": run_id,
        "model": "tfidf_lr",
        "train_seconds": round(train_secs, 2),
        "test_accuracy": round(accuracy, 4),
        "test_macro_f1": round(macro_f1, 4),
        "per_class_f1": {k: round(v, 4) for k, v in per_class_dict.items()},
        "latency_p50_ms": round(p50, 3),
        "latency_p95_ms": round(p95, 3),
        "pipeline_key": key,
        "pipeline_sha256": sha256,
    }
    print("\n--- JSON results (copy into DECISIONS.md) ---")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
