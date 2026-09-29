FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY packages/ packages/
COPY backend/ backend/
COPY services/indexer/ services/indexer/

RUN pip install --no-cache-dir -e packages/agent-sdk
RUN pip install --no-cache-dir -e backend
RUN pip install --no-cache-dir -e services/indexer

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "agentchain_indexer.main"]
