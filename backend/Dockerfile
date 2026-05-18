FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager).
RUN pip install --no-cache-dir uv

# Copy dependency manifests first so Docker caches the install layer.
COPY pyproject.toml ./

# Install all non-dev dependencies directly from pyproject.toml.
RUN uv pip install --no-cache-dir --system .

# Copy application code.
COPY . .

HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/healthz').raise_for_status()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
