FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies required by asyncpg and grpcio
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install shared library first (leverages Docker layer caching)
COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared

# Install cloud-api service
COPY services/cloud-api/ /app/services/cloud-api/
RUN pip install --no-cache-dir /app/services/cloud-api

WORKDIR /app/services/cloud-api

EXPOSE 8080

CMD ["uvicorn", "cloud_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
