FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Copy and install shared library first (better layer caching)
COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared/

# Copy and install the coordination service
COPY services/coordination/ /app/service/
RUN pip install --no-cache-dir /app/service/

EXPOSE 50052 8003

CMD ["python", "-m", "coordination.main"]
