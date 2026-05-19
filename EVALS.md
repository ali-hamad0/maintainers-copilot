# EVALS.md — Evaluation

To be filled in Phase 15.

## Dataset Class Balance

Source: `pandas-dev/pandas` closed issues, label mapping per `DECISIONS.md` §D-P3-02.
Run `python backend/scripts/split_issues.py` to populate real counts.

Post-augmentation split (105 question issues injected via `backend/scripts/inject_questions.py`; 2180 total labeled):

| Class | Train | Val | Test | RAG corpus | Notes |
|---|---|---|---|---|---|
| `bug` | 818 (59.6%) | 147 (56.1%) | 184 (56.3%) | 126 (57.8%) | Regression + Crash labels mapped here |
| `feature` | 205 (14.9%) | 70 (26.7%) | 49 (15.0%) | 53 (24.3%) | Enhancement + Performance + Refactor |
| `docs` | 215 (15.7%) | 40 (15.3%) | 73 (22.3%) | 26 (11.9%) | Docs + Documentation |
| `question` | 135 (9.8%) | 5 (1.9%) | 21 (6.4%) | 13 (6.0%) | Question + Usage Question — was 1 (0.3%) in test before augmentation |
| **Total** | **1373** | **262** | **327** | **218** | 379 discarded (no mappable label) |

Split fractions (time-based, not random): train ~63%, val 12%, test 15%, rag 10%.
Time-order invariant: `max(train.closed_at)=2025-05-20 < min(val.closed_at)=2025-05-21 < min(test.closed_at)=2025-09-30 < min(rag.closed_at)=2026-03-17`.

## Classification Golden Set

**File:** `evals/golden/classification.jsonl`  
**Size:** 25 hand-curated examples  
**Provenance:** Written by the project author (not sampled from any split).  
IDs 90001–90025 are fictitious; there is no overlap with the pandas-dev/pandas
issue IDs used in the train/val/test/rag splits.

**Label distribution:**

| Label    | Count | Notes |
|----------|-------|-------|
| `bug`    | 10    | Includes crashes, data-corruption, regressions, wrong output, silent errors |
| `feature`| 7     | Includes new methods, params, performance requests, format support |
| `docs`   | 5     | Broken link, missing example, typo, outdated info, wrong return type |
| `question`| 3   | Includes one terse two-liner (id 90024) |

**Edge cases deliberately included:**

| ID    | Framing | Correct label | Why it tests the model |
|-------|---------|---------------|------------------------|
| 90009 | "should handle gracefully" phrasing suggests feature, but root cause is a crash | `bug` | Bug framed as expected-behaviour request |
| 90025 | Reported as a docs discrepancy, but the code is wrong | `bug` | Docs/bug ambiguity — label is `bug` because the code contradicts its own docs |
| 90016 | Default-change request with a deprecation timeline | `feature` | Could look like a planning/process issue; it is a feature request |

**Why 25?** Minimum for meaningful macro-F1 with 4 classes (≥5 examples/class on average). More would require proportionally more hand-curation time without a better regression signal at this project scale. 25 is also the per-class size used by many published LLM evaluation benchmarks for rare classes.

**Why hand-curated, not sampled from the test split?** The test split uses GitHub-issued labels which contain noise and ambiguous multi-label issues. Hand-curation guarantees verified ground truth and deliberately includes edge cases that a purely random sample would rarely surface. The golden set provides a qualitatively different signal from the test split: it probes failure modes, not average-case behaviour.

**CI fixture:** `evals/fixtures/train_ci.jsonl` — 40 examples (10/class) committed to the repo. Used by the CI job to retrain TF-IDF+LR inline without needing MinIO, ensuring the eval harness and threshold logic are exercised on every push.

## RAG Golden Set

(25 question/ideal-answer/ground-truth triples)

## Metrics Chosen

**macro-F1** is the primary CI gate metric rather than accuracy because:
1. The label distribution is imbalanced (10 bug / 7 feature / 5 docs / 3 question on the golden set, 59% bug on the test split). Accuracy rewards a model that always predicts "bug" with ~59% on the test split — macro-F1 penalises it with ≈ 0.14.
2. The business cost of mislabelling is symmetric across classes: a feature mislabelled as a bug and a bug mislabelled as a feature are equally costly for triage.
3. macro-F1 is the standard metric reported in the three-way comparison (DECISIONS.md §D-P5-04).

**per-class F1** is reported alongside macro-F1 so regressions can be diagnosed at the class level. The `question` class has 3 golden examples and 21 test examples (post-augmentation). Pre-augmentation question F1 was 0.00 for all models — see DECISIONS.md §D-P7-05 for the fix.

## Judge Model

(LLM judge or RAGAS choice + rationale)

## Hand-Labelled Agreement

(5 of 25, κ or percent agreement with automated judge)

## Thresholds & Rationale

Thresholds calibrated to CI fixture measured performance − 0.05 buffer. See DECISIONS.md §D-P7-02 for derivation.

| Model       | accuracy threshold | f1_macro threshold | Measured (CI fixture, golden set) | Basis |
|-------------|-------------------|--------------------|----------------------------------|-------|
| `tfidf_lr`  | **0.79**          | **0.77**           | acc=0.84, f1=0.8264 (verified exit 0) | CI fixture (40 examples); updated post-augmentation |
| `distilbert`| 0.64              | 0.55               | (requires modelserver) | Pre-augmentation test split; re-eval pending |
| `gemini`    | 0.68              | 0.60               | (requires API key)   | Pre-augmentation test split; re-eval pending |

**Why threshold = measured − 0.05?** The golden set has only 25 examples. One misclassification shifts macro-F1 by ~0.02–0.05. A 0.05 buffer absorbs normal variance while still catching a broken pipeline (random/all-bug → macro-F1 ≈ 0.14–0.22, well below 0.77).

**What a CI failure looks like:**
```
FAIL: tfidf_lr.f1_macro: 0.1429 < threshold 0.77
```
Exit code 1. The message names the model and metric so the developer knows exactly what regressed.

## Regression History

(Any regressions caught in CI, how fixed)
