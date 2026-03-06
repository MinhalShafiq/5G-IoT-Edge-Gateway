FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY simulator/ simulator/

RUN pip install --no-cache-dir .

CMD ["python", "-m", "simulator.main"]
