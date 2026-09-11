FROM cognee/cognee:main

USER root

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Persistent cache for fastembed & HuggingFace models
ENV FASTEMBED_CACHE_PATH=/opt/fastembed_cache
ENV HF_HOME=/opt/hf_home

# ── Memory budget: stay under Render's 512 MB ──
# Single ONNX intra-op thread (default = cpu_count, each thread ~50MB stack)
ENV OMP_NUM_THREADS=1
ENV TOKENIZERS_PARALLELISM=false
# Reduce glibc arena overhead (default 8 arenas × 64MB each)
ENV MALLOC_ARENA_MAX=1
# Force allocations ≥ 32KB through mmap so they're returned to OS on free
ENV MALLOC_MMAP_THRESHOLD_=32768
# Tell ONNX Runtime to use minimal session options
ENV ORT_DISABLE_ALL=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Install solc-select and a pinned solc version
RUN pip install --no-cache-dir solc-select \
    && solc-select install 0.8.20 \
    && solc-select use 0.8.20

# Verify solc is on PATH
RUN solc --version

# The base image's virtual environment does not have pip installed.
# We must install pip into the active virtualenv first, so packages go to the right place.
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python

# Now install Slither and fastembed directly into the active Python environment
RUN python -m pip install --no-cache-dir slither-analyzer 'cognee[fastembed]' fastembed

# Pre-download the embedding model AND its HuggingFace tokenizer so
# nothing is ever fetched from the internet at runtime.
RUN mkdir -p "$FASTEMBED_CACHE_PATH" "$HF_HOME" \
    && slither --version \
    && python -c "\
import os; \
from fastembed import TextEmbedding; \
m = TextEmbedding('BAAI/bge-small-en-v1.5'); \
list(m.embed(['warmup'])); \
print('FASTEMBED ONNX MODEL OK'); \
from huggingface_hub import snapshot_download; \
snapshot_download('BAAI/bge-small-en-v1.5', cache_dir=os.environ['HF_HOME']); \
print('HF TOKENIZER OK')" \
    && chown -R 1000:1000 "$FASTEMBED_CACHE_PATH" "$HF_HOME"

WORKDIR /app

# Ensure Cognee's SQLite and storage directories exist with correct permissions
RUN mkdir -p /app/cognee/.cognee_system/databases /cognee-storage/system/databases \
    && chmod -R 777 /app/cognee/.cognee_system /cognee-storage \
    && chown -R 1000:1000 /app/cognee/.cognee_system /cognee-storage

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY models.py pipeline.py server.py ./

# Switch back to the non-root cognee user from the base image (UID 1000)
USER 1000

# Override whatever CMD/ENTRYPOINT the base image defines
ENTRYPOINT []
EXPOSE 8000
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]