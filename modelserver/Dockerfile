FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Install torch CPU-only first (saves ~2 GB vs the default CUDA wheel).
RUN uv pip install --no-cache-dir --system \
    torch==2.2.2 \
    --index-url https://download.pytorch.org/whl/cpu

COPY . .

RUN uv pip install --no-cache-dir --system .

# Pre-download only the tokenizer + config (~1 MB).
# We do NOT download base model weights — our fine-tuned weights come from MinIO.
RUN python3 -c "\
from transformers import DistilBertTokenizerFast, DistilBertConfig; \
DistilBertTokenizerFast.from_pretrained('distilbert-base-uncased'); \
DistilBertConfig.from_pretrained('distilbert-base-uncased')"

# Download spaCy en_core_web_sm (~12 MB) for the NER endpoint.
RUN python3 -m spacy download en_core_web_sm

HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8001/healthz').raise_for_status()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
