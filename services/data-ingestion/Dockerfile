FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Copy and install shared library first (better layer caching)
COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared/

# Copy and install the data-ingestion service
COPY services/data-ingestion/ /app/service/
RUN pip install --no-cache-dir /app/service/

EXPOSE 8001

CMD ["uvicorn", "data_ingestion.main:app", "--host", "0.0.0.0", "--port", "8001"]
