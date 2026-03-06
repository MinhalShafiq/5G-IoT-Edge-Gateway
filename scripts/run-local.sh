#!/usr/bin/env bash
# Run the full IoT Edge Gateway stack locally with Docker Compose.
# Usage: bash scripts/run-local.sh

set -euo pipefail

echo "=== Starting IoT Edge Gateway ==="

# Start infrastructure first
echo "Starting infrastructure (Redis, PostgreSQL, Mosquitto)..."
docker-compose up -d redis postgres mosquitto

# Wait for infrastructure to be healthy
echo "Waiting for infrastructure to be ready..."
sleep 5

# Start edge services
echo "Starting edge services..."
docker-compose up -d data-ingestion data-persistence

echo ""
echo "=== Services running ==="
echo "  MQTT Broker:     localhost:1883"
echo "  Redis:           localhost:6379"
echo "  PostgreSQL:      localhost:5432"
echo "  Data Ingestion:  http://localhost:8001"
echo ""
echo "To start the simulator: docker-compose --profile simulate up simulator"
echo "To see logs: docker-compose logs -f"
echo "To stop: docker-compose down"
