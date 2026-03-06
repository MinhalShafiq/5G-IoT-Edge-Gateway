#!/usr/bin/env bash
# Deploy to K3s edge cluster using Kustomize.
# Usage: bash scripts/deploy-edge.sh

set -euo pipefail

echo "=== Deploying to K3s Edge Cluster ==="

# Check prerequisites
command -v kubectl >/dev/null 2>&1 || { echo "kubectl required"; exit 1; }

# Verify K3s connectivity
kubectl cluster-info >/dev/null 2>&1 || { echo "Cannot connect to K3s cluster"; exit 1; }

# Build and load Docker images (for local K3s without a registry)
echo "Building Docker images..."
bash scripts/build-images.sh

# Apply Kustomize manifests
echo "Applying K3s manifests..."
kubectl apply -k deploy/k3s/

# Wait for deployments to be ready
echo "Waiting for deployments..."
kubectl -n iot-gateway rollout status deployment/redis --timeout=120s
kubectl -n iot-gateway rollout status deployment/mosquitto --timeout=120s
kubectl -n iot-gateway rollout status deployment/data-ingestion --timeout=120s

echo ""
echo "=== Edge deployment complete ==="
kubectl -n iot-gateway get pods
