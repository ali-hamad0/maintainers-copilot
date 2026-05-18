# EVALS.md — Evaluation

To be filled in Phase 15.

## Dataset Class Balance

Source: `pandas-dev/pandas` closed issues, label mapping per `DECISIONS.md` §D-P3-02.
Run `python backend/scripts/split_issues.py` to populate real counts.

| Class | Train | Val | Test | RAG corpus | Notes |
|---|---|---|---|---|---|
| `bug` | TBD | TBD | TBD | TBD | Regression + Crash labels mapped here |
| `feature` | TBD | TBD | TBD | TBD | Enhancement + Performance + Refactor |
| `docs` | TBD | TBD | TBD | TBD | Docs + Documentation |
| `question` | TBD | TBD | TBD | TBD | Question + Usage Question |
| **Total** | **TBD** | **TBD** | **TBD** | **TBD** | Rows with no mappable label discarded |

Split fractions (time-based, not random): train ~63%, val 12%, test 15%, rag 10%.
Time-order invariant: `max(train.closed_at) < min(val.closed_at) < min(test.closed_at) < min(rag.closed_at)`.

## Classification Golden Set

(Provenance, 25 examples, coverage)

## RAG Golden Set

(25 question/ideal-answer/ground-truth triples)

## Metrics Chosen

(Why these metrics, not others)

## Judge Model

(LLM judge or RAGAS choice + rationale)

## Hand-Labelled Agreement

(5 of 25, κ or percent agreement with automated judge)

## Thresholds & Rationale

(Why X for macro-F1, why Y for hit@5)

## Regression History

(Any regressions caught in CI, how fixed)
