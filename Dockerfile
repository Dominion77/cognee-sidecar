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

# Install Slither
RUN pip install --no-cache-dir slither-analyzer

# Verify Slither is on PATH
RUN slither --version

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY models.py pipeline.py server.py ./

ENTRYPOINT []
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]