FROM cognee/cognee:main

USER root

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Persistent cache for fastembed & HuggingFace models
ENV FASTEMBED_CACHE_PATH=/opt/fastembed_cache
ENV HF_HOME=/opt/hf_home

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

# Pre-download the embedding model into the persistent cache so it is
# baked into the image layer and never fetched from HuggingFace at runtime.
RUN mkdir -p "$FASTEMBED_CACHE_PATH" "$HF_HOME" \
    && slither --version \
    && python -c "\
from fastembed import TextEmbedding; \
m = TextEmbedding('BAAI/bge-small-en-v1.5'); \
list(m.embed(['warmup'])); \
print('FASTEMBED MODEL PRE-DOWNLOADED!')" \
    && chmod -R a+rX "$FASTEMBED_CACHE_PATH" "$HF_HOME"

WORKDIR /app

# Ensure Cognee's SQLite database directory exists with correct permissions
RUN mkdir -p /app/cognee/.cognee_system/databases \
    && chmod -R 777 /app/cognee/.cognee_system

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY models.py pipeline.py server.py ./

# Switch back to the non-root cognee user from the base image (UID 1000)
USER 1000

# Override whatever CMD/ENTRYPOINT the base image defines
ENTRYPOINT []
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]