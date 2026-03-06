FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Copy and install shared library first (better layer caching)
COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared

# Copy and install service
COPY services/ml-inference/ /app/service/
RUN pip install --no-cache-dir /app/service

# Create model store directory
RUN mkdir -p /app/model_store

EXPOSE 8004

CMD ["uvicorn", "ml_inference.main:app", "--host", "0.0.0.0", "--port", "8004"]
