FROM cognee/cognee:main

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# System dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Install solc static binary (Solidity compiler required by Slither).
# We pin 0.8.20 as default. Slither will warn on pragma mismatches for
# older contracts but will still analyse them.
RUN curl -fsSL \
    "https://github.com/ethereum/solidity/releases/download/v0.8.20/solc-static-linux" \
    -o /usr/local/bin/solc \
    && chmod +x /usr/local/bin/solc

# Verify solc is on PATH
RUN solc --version

# Install Slither on top of the cognee base image
RUN pip install --no-cache-dir slither-analyzer

# Verify Slither is on PATH
RUN slither --version

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY models.py pipeline.py server.py ./

# Override whatever CMD/ENTRYPOINT the base image defines.
# We run our own FastAPI server — not Cognee's built-in server.
ENTRYPOINT []
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]