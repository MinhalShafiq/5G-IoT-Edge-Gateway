FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies required by grpcio
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Install shared library first (leverages Docker layer caching)
COPY shared/ /app/shared/
RUN pip install --no-cache-dir /app/shared

# Install scheduler service
COPY services/scheduler/ /app/services/scheduler/
RUN pip install --no-cache-dir /app/services/scheduler

WORKDIR /app/services/scheduler

EXPOSE 8005 50053

CMD ["python", "-m", "scheduler.main"]
