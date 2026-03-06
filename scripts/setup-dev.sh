#!/usr/bin/env bash
# Set up local development environment.
# Usage: bash scripts/setup-dev.sh

set -euo pipefail

echo "=== IoT Edge Gateway — Development Setup ==="

# Check prerequisites
command -v python3.10 >/dev/null 2>&1 || { echo "Python 3.10 required"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "Docker required"; exit 1; }
command -v docker-compose >/dev/null 2>&1 || command -v docker compose >/dev/null 2>&1 || { echo "Docker Compose required"; exit 1; }

# Create virtual environment
echo "Creating virtual environment..."
python3.10 -m venv .venv
source .venv/bin/activate || source .venv/Scripts/activate

# Install shared library
echo "Installing shared library..."
pip install -e shared/

# Install all services in editable mode
for svc in services/*/; do
    if [ -f "$svc/pyproject.toml" ]; then
        echo "Installing $(basename "$svc")..."
        pip install -e "$svc" 2>/dev/null || echo "  Skipped (missing deps)"
    fi
done

# Install simulator
echo "Installing simulator..."
pip install -e simulator/

# Install dev tools
echo "Installing dev tools..."
pip install pytest pytest-asyncio pytest-cov httpx ruff locust

# Copy env file
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example"
fi

echo ""
echo "=== Setup complete ==="
echo "Activate the venv: source .venv/bin/activate"
echo "Start infrastructure: docker-compose up -d redis postgres mosquitto"
echo "Run a service: uvicorn data_ingestion.main:app --port 8001 --reload"
echo "Run simulator: python -m simulator.main"
