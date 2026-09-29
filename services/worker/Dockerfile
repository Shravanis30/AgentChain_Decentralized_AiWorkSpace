FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY packages/ packages/
COPY backend/ backend/
COPY services/worker/ services/worker/

RUN pip install --no-cache-dir -e packages/agent-sdk
RUN pip install --no-cache-dir -e backend
RUN pip install --no-cache-dir -e services/worker

ENV PYTHONUNBUFFERED=1
ENV WORKER_SANDBOX_TYPE=local

CMD ["python", "-m", "agentchain_worker.main"]
