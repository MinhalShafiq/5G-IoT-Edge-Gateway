FROM python:3.11-slim

WORKDIR /app

# System deps for asyncpg, grpc, etc.
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

# Install shared library
COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared/

# Install all services
COPY services/data-ingestion/ /app/services/data-ingestion/
COPY services/data-persistence/ /app/services/data-persistence/
COPY services/device-manager/ /app/services/device-manager/
COPY services/cloud-api/ /app/services/cloud-api/
COPY services/coordination/ /app/services/coordination/
COPY services/middleware/ /app/services/middleware/
COPY services/scheduler/ /app/services/scheduler/
COPY services/ml-inference/ /app/services/ml-inference/
COPY services/ml-training/ /app/services/ml-training/
COPY services/batch-analytics/ /app/services/batch-analytics/

RUN for svc in /app/services/*/; do \
      if [ -f "$svc/pyproject.toml" ]; then \
        pip install --no-cache-dir "$svc" 2>/dev/null || true; \
      fi; \
    done

# Install simulator
COPY simulator/ /app/simulator/
RUN pip install --no-cache-dir /app/simulator/ || true

# Install test tools
RUN pip install --no-cache-dir pytest pytest-asyncio pytest-cov httpx locust

# Copy tests
COPY tests/ /app/tests/

CMD ["python", "-m", "pytest", "tests/", "-v", "--tb=short", "--no-header"]
