"""DistilBERT 4-class issue classifier.

Loaded once in lifespan and stored in app.state.classifier.
All inference is CPU-bound; wrapped with asyncio.to_thread so the event loop
is never blocked.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import torch
from transformers import (
    DistilBertConfig,
    DistilBertForSequenceClassification,
    DistilBertTokenizerFast,
)

_MODEL_NAME = "distilbert-base-uncased"
_MAX_LENGTH = 128
_LABEL2ID: dict[str, int] = {"bug": 0, "feature": 1, "docs": 2, "question": 3}
_ID2LABEL: dict[int, str] = {v: k for k, v in _LABEL2ID.items()}


@dataclass(frozen=True)
class ClassifyResult:
    label: str
    score: float
    scores: dict[str, float]


class ClassifierService:
    """Wraps a loaded DistilBERT model for synchronous and async inference."""

    def __init__(self, weights_path: Path) -> None:
        self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._tokenizer = DistilBertTokenizerFast.from_pretrained(_MODEL_NAME)
        # Build model from config only — no HuggingFace weight download needed.
        # Our fine-tuned weights are loaded from MinIO via weights_path.
        config = DistilBertConfig.from_pretrained(
            _MODEL_NAME,
            num_labels=len(_LABEL2ID),
            id2label=_ID2LABEL,
            label2id=_LABEL2ID,
        )
        self._model = DistilBertForSequenceClassification(config)
        state = torch.load(weights_path, map_location=self._device, weights_only=True)
        self._model.load_state_dict(state)
        self._model.to(self._device)
        self._model.eval()

    def _classify_sync(self, text: str) -> ClassifyResult:
        enc = self._tokenizer(
            text,
            truncation=True,
            padding="max_length",
            max_length=_MAX_LENGTH,
            return_tensors="pt",
        )
        input_ids = enc["input_ids"].to(self._device)
        attention_mask = enc["attention_mask"].to(self._device)

        with torch.no_grad():
            logits = self._model(input_ids=input_ids, attention_mask=attention_mask).logits

        probs = torch.softmax(logits, dim=-1).squeeze().tolist()
        scores = {_ID2LABEL[i]: float(probs[i]) for i in range(len(_ID2LABEL))}
        top_id = int(torch.argmax(logits, dim=-1).item())
        return ClassifyResult(
            label=_ID2LABEL[top_id],
            score=float(probs[top_id]),
            scores=scores,
        )

    async def classify(self, text: str) -> ClassifyResult:
        """Classify *text* off the event loop thread."""
        return await asyncio.to_thread(self._classify_sync, text)
