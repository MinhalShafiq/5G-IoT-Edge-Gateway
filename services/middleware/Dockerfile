FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install shared library first (layer caching)
COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared

# Install service dependencies
COPY services/middleware/pyproject.toml /app/services/middleware/
RUN pip install --no-cache-dir /app/services/middleware/

# Copy service code
COPY services/middleware/middleware/ /app/services/middleware/middleware/

WORKDIR /app/services/middleware

EXPOSE 8000

CMD ["uvicorn", "middleware.main:app", "--host", "0.0.0.0", "--port", "8000"]
