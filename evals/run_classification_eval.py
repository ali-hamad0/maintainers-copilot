#!/usr/bin/env python3
"""Classification golden-set evaluator.

Loads evals/golden/classification.jsonl (25 hand-curated examples), runs each
of the three classifiers, computes macro-F1 + per-class F1 + confusion matrix
per model, checks against backend/eval_thresholds.yaml, and uploads the
report to MinIO.

Exit 0  = all models that ran passed their thresholds (or were explicitly skipped).
Exit 1  = one or more model thresholds regressed.

Environment variables
---------------------
MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_LOCAL_ENDPOINT, MINIO_BUCKET
    MinIO credentials for downloading the train split + uploading the report.
    If absent, MinIO upload is skipped; TF-IDF+LR falls back to the CI fixture.

MODELSERVER_URL  (default: http://localhost:8001)
    DistilBERT endpoint.  Skipped if the server is unreachable.

GEMINI_API_KEY
    Gemini API key.  Skipped if absent.  Alternative: set VAULT_ROOT_TOKEN +
    VAULT_ADDR to fetch the key from Vault.

GIT_SHA
    Commit SHA embedded in the MinIO upload path.  Defaults to 'git rev-parse'.

Flags
-----
--use-ci-fixture    Retrain TF-IDF+LR from evals/fixtures/train_ci.jsonl
                    (40 examples, committed to repo) instead of MinIO.
--skip-distilbert   Skip the DistilBERT model entirely.
--skip-gemini       Skip the Gemini model entirely.
--skip-minio-upload Do not upload the report to MinIO.
--report-path PATH  Where to write the JSON report (default: eval_report.json).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.pipeline import Pipeline

REPO_ROOT = Path(__file__).parent.parent
GOLDEN_PATH = Path(__file__).parent / "golden" / "classification.jsonl"
CI_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "train_ci.jsonl"
THRESHOLDS_PATH = REPO_ROOT / "backend" / "eval_thresholds.yaml"
LABELS: list[str] = ["bug", "feature", "docs", "question"]
SPLIT_PREFIX = "splits/v1"
GEMINI_MODEL = "gemini-2.5-flash"
_CLASSIFY_PROMPT = """\
Classify this GitHub issue as exactly one of: bug, feature, docs, question.

bug: incorrect behaviour, crash, regression, or unexpected output
feature: request for new functionality, performance improvement, or refactoring
docs: concerns only documentation, examples, or text — no code change
question: asks for usage help or clarification — no code change implied

Reply with ONLY the single word label, nothing else.

Issue:
{text}"""
_VALID_LABELS: frozenset[str] = frozenset({"bug", "feature", "docs", "question"})


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return os.getenv("GIT_SHA", "unknown")


# ---------------------------------------------------------------------------
# MinIO helpers
# ---------------------------------------------------------------------------


def _minio_cfg_from_env() -> dict[str, str] | None:
    keys = [
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_LOCAL_ENDPOINT",
        "MINIO_BUCKET",
    ]
    if not all(os.getenv(k) for k in keys):
        return None
    return {k: os.environ[k] for k in keys}


def _build_minio_client(cfg: dict[str, str]) -> Any:
    from minio import Minio

    endpoint = (
        cfg["MINIO_LOCAL_ENDPOINT"].removeprefix("http://").removeprefix("https://")
    )
    secure = not (endpoint.startswith("localhost") or endpoint.startswith("127."))
    return Minio(
        endpoint,
        access_key=cfg["MINIO_ACCESS_KEY"],
        secret_key=cfg["MINIO_SECRET_KEY"],
        secure=secure,
    )


def _download_train_split(cfg: dict[str, str]) -> list[dict[str, Any]] | None:
    try:
        client = _build_minio_client(cfg)
        key = f"{SPLIT_PREFIX}/train.jsonl"
        response = client.get_object(cfg["MINIO_BUCKET"], key)
        return [
            json.loads(line)
            for line in response.read().decode("utf-8").splitlines()
            if line.strip()
        ]
    except Exception as exc:
        print(
            f"  WARN: could not download train split from MinIO: {exc}", file=sys.stderr
        )
        return None


def _upload_report(cfg: dict[str, str], git_sha: str, report_bytes: bytes) -> None:
    client = _build_minio_client(cfg)
    key = f"evals/{git_sha}/classification.json"
    client.put_object(
        cfg["MINIO_BUCKET"],
        key,
        io.BytesIO(report_bytes),
        length=len(report_bytes),
        content_type="application/json",
    )
    print(f"  Uploaded -> minio:{key}")


# ---------------------------------------------------------------------------
# Model runners
# ---------------------------------------------------------------------------


def _build_tfidf_pipeline(min_df: int) -> Pipeline:
    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    max_features=50_000,
                    sublinear_tf=True,
                    min_df=min_df,
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


def run_tfidf_lr(
    golden_texts: list[str],
    *,
    use_ci_fixture: bool,
    minio_cfg: dict[str, str] | None,
) -> list[str] | None:
    if use_ci_fixture:
        train_rows = load_jsonl(CI_FIXTURE_PATH)
        min_df = 1
        print(f"  Training on CI fixture ({len(train_rows)} examples, min_df=1)")
    elif minio_cfg is not None:
        train_rows_or_none = _download_train_split(minio_cfg)
        if train_rows_or_none is None:
            print(
                "  SKIP: MinIO download failed; run with --use-ci-fixture for offline mode"
            )
            return None
        train_rows = train_rows_or_none
        min_df = 2
        print(f"  Training on MinIO train split ({len(train_rows)} examples, min_df=2)")
    else:
        print(
            "  SKIP: no training data (set MINIO_* env vars or pass --use-ci-fixture)",
            file=sys.stderr,
        )
        return None

    texts_train = [str(r["text"]) for r in train_rows]
    labels_train = [str(r["label"]) for r in train_rows]
    pipeline = _build_tfidf_pipeline(min_df)
    t0 = time.perf_counter()
    pipeline.fit(texts_train, labels_train)
    print(f"  Fit in {(time.perf_counter() - t0) * 1000:.0f} ms")
    return list(pipeline.predict(golden_texts))


def run_distilbert(golden_texts: list[str], url: str) -> list[str] | None:
    try:
        with httpx.Client(timeout=30.0) as client:
            health = client.get(f"{url}/healthz")
            if health.status_code != 200:
                print(
                    f"  SKIP: modelserver at {url} returned HTTP {health.status_code}",
                    file=sys.stderr,
                )
                return None
            predictions: list[str] = []
            for text in golden_texts:
                resp = client.post(f"{url}/v1/classify", json={"text": text})
                resp.raise_for_status()
                predictions.append(resp.json()["label"])
            return predictions
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
        print(f"  SKIP: modelserver not reachable at {url} — {exc}", file=sys.stderr)
        return None


def _fetch_gemini_key_from_vault() -> str | None:
    vault_token = os.getenv("VAULT_ROOT_TOKEN", "dev-root-token")
    vault_addr = os.getenv("VAULT_ADDR", "http://localhost:8200")
    try:
        resp = httpx.get(
            f"{vault_addr}/v1/secret/data/gemini",
            headers={"X-Vault-Token": vault_token},
            timeout=3.0,
        )
        resp.raise_for_status()
        key: str = resp.json()["data"]["data"]["api_key"]
        return None if key.startswith("AIza-placeholder") else key
    except Exception:
        return None


def run_gemini(golden_texts: list[str], api_key: str) -> list[str] | None:
    try:
        import google.genai as genai  # type: ignore[import]
        from google.genai import types  # type: ignore[import]
    except ImportError:
        print("  SKIP: google-genai not installed", file=sys.stderr)
        return None

    gemini_client = genai.Client(api_key=api_key)
    predictions: list[str] = []
    errors = 0
    for i, text in enumerate(golden_texts):
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=_CLASSIFY_PROMPT.format(text=text[:3000]),
                config=types.GenerateContentConfig(temperature=0.0),
            )
            raw = (response.text or "").strip().lower()
            label = next((lbl for lbl in _VALID_LABELS if lbl == raw), None) or next(
                (lbl for lbl in _VALID_LABELS if lbl in raw), "bug"
            )
            predictions.append(label)
        except Exception as exc:
            print(f"  WARN: example {i} failed: {exc}", file=sys.stderr)
            predictions.append("bug")
            errors += 1
    if errors:
        print(f"  {errors} API errors (fell back to 'bug')", file=sys.stderr)
    return predictions


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def compute_metrics(y_true: list[str], y_pred: list[str]) -> dict[str, Any]:
    per_class = f1_score(y_true, y_pred, average=None, labels=LABELS, zero_division=0)
    conf = confusion_matrix(y_true, y_pred, labels=LABELS)
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "f1_macro": round(
            float(
                f1_score(
                    y_true, y_pred, average="macro", labels=LABELS, zero_division=0
                )
            ),
            4,
        ),
        "per_class_f1": {
            LABELS[i]: round(float(per_class[i]), 4) for i in range(len(LABELS))
        },
        "confusion_matrix": conf.tolist(),
        "n_samples": len(y_true),
    }


# ---------------------------------------------------------------------------
# Threshold gate
# ---------------------------------------------------------------------------


def check_thresholds(report: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    model_thresholds: dict[str, Any] = thresholds.get("classification", {}).get(
        "models", {}
    )
    for model_key, model_metrics in report["models"].items():
        if not model_metrics.get("ran"):
            continue
        per_model = model_thresholds.get(model_key, {})
        for metric, threshold in per_model.items():
            actual = model_metrics.get(metric)
            if actual is not None and float(actual) < float(threshold):
                failures.append(
                    f"{model_key}.{metric}: {actual:.4f} < threshold {threshold}"
                )
    return failures


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classification golden-set evaluator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--use-ci-fixture",
        action="store_true",
        help="Retrain TF-IDF+LR on evals/fixtures/train_ci.jsonl instead of MinIO",
    )
    parser.add_argument("--skip-distilbert", action="store_true")
    parser.add_argument("--skip-gemini", action="store_true")
    parser.add_argument("--skip-minio-upload", action="store_true")
    parser.add_argument(
        "--report-path",
        default="eval_report.json",
        help="Path to write the JSON report (default: eval_report.json)",
    )
    args = parser.parse_args(argv)

    golden = load_jsonl(GOLDEN_PATH)
    if not golden:
        print(f"ERROR: golden set is empty at {GOLDEN_PATH}", file=sys.stderr)
        return 2
    texts = [str(r["text"]) for r in golden]
    y_true = [str(r["label"]) for r in golden]
    from collections import Counter

    label_counts = dict(Counter(y_true))
    print(f"Loaded {len(golden)} golden examples  (labels: {label_counts})")

    thresholds: dict[str, Any] = yaml.safe_load(
        THRESHOLDS_PATH.read_text(encoding="utf-8")
    )
    minio_cfg = _minio_cfg_from_env()
    git_sha = _git_sha()

    report: dict[str, Any] = {
        "git_sha": git_sha,
        "n_golden": len(golden),
        "models": {},
    }

    # TF-IDF + LR
    print("\n[1/3] TF-IDF + LogisticRegression")
    tfidf_preds = run_tfidf_lr(
        texts, use_ci_fixture=args.use_ci_fixture, minio_cfg=minio_cfg
    )
    if tfidf_preds is not None:
        m = compute_metrics(y_true, tfidf_preds)
        report["models"]["tfidf_lr"] = {"ran": True, **m}
        print(f"  accuracy={m['accuracy']}  macro_f1={m['f1_macro']}")
        for cls, score in m["per_class_f1"].items():
            print(f"    {cls:<10} F1={score:.4f}")
    else:
        report["models"]["tfidf_lr"] = {"ran": False}

    # DistilBERT
    print("\n[2/3] DistilBERT (modelserver)")
    if args.skip_distilbert:
        print("  Skipped by --skip-distilbert flag")
        report["models"]["distilbert"] = {"ran": False, "skipped_by_flag": True}
    else:
        modelserver_url = os.getenv("MODELSERVER_URL", "http://localhost:8001")
        bert_preds = run_distilbert(texts, modelserver_url)
        if bert_preds is not None:
            m = compute_metrics(y_true, bert_preds)
            report["models"]["distilbert"] = {"ran": True, **m}
            print(f"  accuracy={m['accuracy']}  macro_f1={m['f1_macro']}")
            for cls, score in m["per_class_f1"].items():
                print(f"    {cls:<10} F1={score:.4f}")
        else:
            report["models"]["distilbert"] = {"ran": False}

    # Gemini
    print("\n[3/3] Gemini 2.5 Flash")
    if args.skip_gemini:
        print("  Skipped by --skip-gemini flag")
        report["models"]["gemini"] = {"ran": False, "skipped_by_flag": True}
    else:
        gemini_key = os.getenv("GEMINI_API_KEY") or _fetch_gemini_key_from_vault()
        if gemini_key:
            gemini_preds = run_gemini(texts, gemini_key)
            if gemini_preds is not None:
                m = compute_metrics(y_true, gemini_preds)
                report["models"]["gemini"] = {"ran": True, **m}
                print(f"  accuracy={m['accuracy']}  macro_f1={m['f1_macro']}")
                for cls, score in m["per_class_f1"].items():
                    print(f"    {cls:<10} F1={score:.4f}")
            else:
                report["models"]["gemini"] = {"ran": False}
        else:
            print("  SKIP: GEMINI_API_KEY not set and Vault unreachable")
            report["models"]["gemini"] = {"ran": False}

    # Threshold check
    failures = check_thresholds(report, thresholds)
    report["threshold_failures"] = failures
    report["passed"] = not failures

    # Write report
    report_bytes = json.dumps(report, indent=2).encode("utf-8")
    report_path = Path(args.report_path)
    report_path.write_bytes(report_bytes)
    print(f"\nReport -> {report_path}")

    # Upload to MinIO
    if not args.skip_minio_upload and minio_cfg is not None:
        try:
            _upload_report(minio_cfg, git_sha, report_bytes)
        except Exception as exc:
            print(f"WARN: MinIO upload failed: {exc}", file=sys.stderr)

    # Final verdict
    ran_models = [k for k, v in report["models"].items() if v.get("ran")]
    if not ran_models:
        print(
            "\nWARN: no models ran — cannot enforce threshold gate "
            "(start modelserver or set GEMINI_API_KEY or pass --use-ci-fixture)"
        )
        return 0

    if failures:
        print(f"\nFAIL -- {len(failures)} threshold regression(s):")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1

    print(f"\nPASS -- all thresholds met  [models: {', '.join(ran_models)}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
