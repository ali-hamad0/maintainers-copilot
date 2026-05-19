# Model Card — Maintainer's Copilot Issue Classifier

## Model

- **Architecture:** `distilbert-base-uncased` with a sequence-classification head on the `[CLS]` token
- **Task:** 4-class GitHub issue triage — `bug` / `feature` / `docs` / `question`
- **Parameter count:** ~66 M (base) + 4-class head (~3 K)
- **Library:** HuggingFace `transformers` — `DistilBertForSequenceClassification`

## Dataset

- **Source:** `pandas-dev/pandas` closed GitHub issues (via REST API)
- **Label mapping:** See `DECISIONS.md` §D-P3-02 for full mapping table
- **Filtering:** Issues with no label mappable to the four target classes are discarded
- **Input format:** `"TITLE\n\nBODY"` (body may be empty), truncated to 128 tokens

## Splits

| Split | Fraction | Purpose |
|---|---|---|
| Train | ~63% (oldest) | Fine-tune weights |
| Val | 12% | Early stopping, hyperparameter tuning |
| Test | 15% | Final evaluation; CI gate threshold |
| RAG corpus | 10% (newest) | Dense retrieval index; excluded from classifier splits |

**Split strategy:** Time-based — sorted by `closed_at` ascending. No random shuffling of boundaries.
See `DECISIONS.md` §D-P3-03 for the rationale.

### SHA-256 Hashes

| Split | SHA-256 |
|---|---|
| `train` | `ac5c246f9a9d7e063a08ef657e124519f998faea5010008baff9f28570f66283` |
| `val` | `e761bd4f311ea8425e6a08a71025573c4e67b00d7e78883e238a8bf8fa0b1fcc` |
| `test` | `d7b07af4318852c8f6cd82c70fb9842b8e9d178ec14504e2e99d265c17cc9f84` |
| `rag_corpus` | `5dde72fbe9105d0ca3f7ce7d80a826aa9d13ffb91639d89a4b31561b31e6ab28` |

## Architecture

```
DistilBertModel (6 transformer blocks)
  └── pre_classifier  (Linear 768 → 768, ReLU)
  └── classifier      (Linear 768 → 4)
  └── dropout
```

Input token IDs and attention mask from tokenizer (max_length=128).
The `[CLS]` token representation after the final transformer block is passed to the head.

## Freeze Policy

All DistilBERT parameters are **frozen** except:
- Transformer block 5 (index 5 of 6, zero-indexed) — the final block
- `pre_classifier` + `classifier` + `dropout` in the head

**Rationale:** Unfreezing only the last block (~7 M / 66 M params) lets the model adapt the
task-specific representation without catastrophic forgetting of general language knowledge,
and keeps Colab T4 training under ~20 minutes. See `DECISIONS.md` §D-P3-06.

## Hyperparameters

| Parameter | Value | Rationale |
|---|---|---|
| `max_length` | 128 | Covers >95% of issue title+body pairs at this token budget |
| `batch_size` | 32 | Fits T4 16 GB VRAM with gradient checkpointing off |
| `epochs` | 5 | Empirically sufficient for DistilBERT fine-tuning at this scale |
| `optimizer` | AdamW | Standard for transformer fine-tuning |
| `learning_rate` | 2e-5 | BERT-family sweet spot; avoids instability with partial freeze |
| `weight_decay` | 0.01 | Light regularisation |
| `warmup_steps` | 500 | ~1.6 epochs at batch=32, ~10k train examples; stabilises head first |
| `scheduler` | Linear decay with warmup | Reduces LR to 0 by end of training |
| `random_state` | 42 | DataLoader shuffle seed only — split boundaries are time-based |

## Label Encoding

| Class | Label ID |
|---|---|
| `bug` | 0 |
| `feature` | 1 |
| `docs` | 2 |
| `question` | 3 |

## Training Hardware

- **Platform:** Google Colab free tier
- **GPU:** T4 (16 GB VRAM)
- **Estimated training time:** ~15–20 minutes per run

## Weights

- **MinIO path:** `models/classifier/weights.pt`
- **Weights SHA-256:** `527da66c84c29cb5eeefdbd72370535a4261e9f217c27bd348c13df22013aa63`
- **Run ID:** `1f610ed8-b3a0-4a96-b301-5fe445813019`
- **Training duration:** ~18 minutes on Colab T4

## Evaluation Results (final weights, run `1f610ed8`)

| Split | Accuracy | Macro-F1 | Bug F1 | Feature F1 | Docs F1 | Question F1 |
|---|---|---|---|---|---|---|
| Val | 0.8755 | 0.6540 | 0.91 | 0.81 | 0.89 | 0.00 |
| Test | 0.8939 | 0.6483 | 0.93 | 0.76 | 0.91 | 0.00 |

**Note:** `question` F1 = 0.00 on both splits due to severe class imbalance (55/1307 train = 4.2%, 1/311 test = 0.3%). The macro-F1 threshold is calibrated accordingly — see `DECISIONS.md` §D-P4-09.

Confusion matrix: `evals/artefacts/confusion_matrix.png`

## Evaluation Thresholds (CI gate)

Defined in `backend/eval_thresholds.yaml`:

| Metric | Threshold | Rationale |
|---|---|---|
| `classification.accuracy` | 0.70 | Model achieves 0.89 — comfortable margin |
| `classification.f1_macro` | 0.62 | Calibrated to actual 0.6483; `question` class has 1 test sample, F1=0.00 by definition |

## Limitations

- Trained on pandas-specific issues; may under-perform on other OSS repos without fine-tuning
- Bodies longer than 128 tokens are truncated; very detailed bug reports may lose tail context
- Labels are maintainer-applied; noisy labels in the source repo propagate into training data
