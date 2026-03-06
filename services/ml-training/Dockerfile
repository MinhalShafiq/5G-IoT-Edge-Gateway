FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies required by asyncpg and torch
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install shared library first (leverages Docker layer caching)
COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared

# Install ml-training service
COPY services/ml-training/ /app/services/ml-training/
RUN pip install --no-cache-dir /app/services/ml-training

# Create model output directory
RUN mkdir -p /models

WORKDIR /app/services/ml-training

EXPOSE 8006

CMD ["uvicorn", "ml_training.main:app", "--host", "0.0.0.0", "--port", "8006"]
