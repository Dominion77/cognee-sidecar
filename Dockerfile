FROM cognee/cognee:main

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# System dependencies + Node.js for solcjs
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        git \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# Install solc via npm
RUN npm install -g solc

# Verify solcjs is available
RUN node -e "require('solc')" && echo "solcjs OK"

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