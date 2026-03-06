FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies: Java 17 for PySpark, gcc/libpq for asyncpg
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc libpq-dev openjdk-17-jre-headless && \
    rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

# Install shared library first (leverages Docker layer caching)
COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared

# Install batch-analytics service
COPY services/batch-analytics/ /app/services/batch-analytics/
RUN pip install --no-cache-dir /app/services/batch-analytics

WORKDIR /app/services/batch-analytics

EXPOSE 8007

CMD ["uvicorn", "batch_analytics.main:app", "--host", "0.0.0.0", "--port", "8007"]
