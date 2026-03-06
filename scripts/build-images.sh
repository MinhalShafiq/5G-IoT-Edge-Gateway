#!/usr/bin/env bash
# Build all Docker images for the IoT Edge Gateway.
# Usage: bash scripts/build-images.sh [--push]

set -euo pipefail

REGISTRY="${REGISTRY:-iot-gateway}"
TAG="${TAG:-latest}"
PUSH="${1:-}"

echo "=== Building Docker images ==="
echo "Registry: $REGISTRY"
echo "Tag: $TAG"

# Infrastructure
echo "Building mqtt-broker..."
docker build -t "$REGISTRY/mqtt-broker:$TAG" services/mqtt-broker/

# Edge services (require build context at project root for shared/)
echo "Building data-ingestion..."
docker build -t "$REGISTRY/data-ingestion:$TAG" -f services/data-ingestion/Dockerfile .

echo "Building data-persistence..."
docker build -t "$REGISTRY/data-persistence:$TAG" -f services/data-persistence/Dockerfile .

echo "Building device-manager..."
docker build -t "$REGISTRY/device-manager:$TAG" -f services/device-manager/Dockerfile .

echo "Building ml-inference..."
docker build -t "$REGISTRY/ml-inference:$TAG" -f services/ml-inference/Dockerfile .

echo "Building coordination..."
docker build -t "$REGISTRY/coordination:$TAG" -f services/coordination/Dockerfile .

echo "Building scheduler..."
docker build -t "$REGISTRY/scheduler:$TAG" -f services/scheduler/Dockerfile .

echo "Building middleware..."
docker build -t "$REGISTRY/middleware:$TAG" -f services/middleware/Dockerfile .

# Cloud services
echo "Building cloud-api..."
docker build -t "$REGISTRY/cloud-api:$TAG" -f services/cloud-api/Dockerfile .

echo "Building ml-training..."
docker build -t "$REGISTRY/ml-training:$TAG" -f services/ml-training/Dockerfile .

echo "Building batch-analytics..."
docker build -t "$REGISTRY/batch-analytics:$TAG" -f services/batch-analytics/Dockerfile .

# Simulator
echo "Building simulator..."
docker build -t "$REGISTRY/simulator:$TAG" simulator/

echo ""
echo "=== All images built ==="
docker images | grep "$REGISTRY"

if [ "$PUSH" = "--push" ]; then
    echo "Pushing images..."
    for img in mqtt-broker data-ingestion data-persistence device-manager ml-inference coordination scheduler middleware cloud-api ml-training batch-analytics simulator; do
        docker push "$REGISTRY/$img:$TAG"
    done
fi
