FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for asyncpg
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy and install shared library first (better layer caching)
COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared/

# Copy service code and install
COPY services/data-persistence/ /app/services/data-persistence/
RUN pip install --no-cache-dir /app/services/data-persistence/

CMD ["python", "-m", "data_persistence.main"]
