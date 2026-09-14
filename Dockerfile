FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# ── Memory budget: stay under Render's 512 MB ──
# Single ONNX intra-op thread (safety net if any dep pulls onnx transitively)
ENV OMP_NUM_THREADS=1
ENV TOKENIZERS_PARALLELISM=false
# Reduce glibc arena overhead (default 8 arenas × 64MB each)
ENV MALLOC_ARENA_MAX=1
# Force allocations ≥ 32KB through mmap so they're returned to OS on free
ENV MALLOC_MMAP_THRESHOLD_=32768
# Tell LanceDB to tolerate fork() — we only fork+exec for slither CLI
ENV LANCEDB_FORK_SUPPORT=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Install solc-select and a pinned solc version
# solc-select puts the active solc shim in ~/.solc-select/global-version
# and the actual binary in ~/.solc-select/artifacts/
ENV PATH="/root/.solc-select/artifacts/solc-0.8.20:${PATH}"
RUN pip install --no-cache-dir solc-select \
    && solc-select install 0.8.20 \
    && solc-select use 0.8.20

# Verify solc is on PATH
RUN solc --version

# Install Slither
RUN pip install --no-cache-dir slither-analyzer

WORKDIR /app

# Ensure Cognee's SQLite and storage directories exist with correct permissions
RUN mkdir -p /app/cognee/.cognee_system/databases /cognee-storage/system/databases \
    && chmod -R 777 /app/cognee/.cognee_system /cognee-storage

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY models.py pipeline.py server.py ./

EXPOSE 8000
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]