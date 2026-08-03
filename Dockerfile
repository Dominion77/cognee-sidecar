FROM cognee/cognee:main

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

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

# Install Slither and fastembed on top of the cognee base image
RUN pip install --no-cache-dir slither-analyzer fastembed

# Verify Slither is on PATH
RUN slither --version

WORKDIR /app

# Ensure Cognee's SQLite database directory exists with correct permissions
RUN mkdir -p /app/cognee/.cognee_system/databases \
    && chmod -R 777 /app/cognee/.cognee_system

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY models.py pipeline.py server.py ./

# Override whatever CMD/ENTRYPOINT the base image defines
ENTRYPOINT []
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]