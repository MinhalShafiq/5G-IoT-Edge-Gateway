#!/usr/bin/env bash
# Train the PyTorch anomaly autoencoder and export it to ONNX.
#
# The ml-inference Docker image runs this automatically in its build stage;
# use this script to retrain locally and inspect the metrics.
# Requires: torch, onnxruntime, numpy, pydantic, structlog (see services/ml-inference/Dockerfile)
# Usage: bash scripts/train-baseline-model.sh [output_dir]

set -euo pipefail

OUTPUT_DIR="${1:-model_store}"

echo "=== Training PyTorch anomaly autoencoder ==="

PYTHONPATH="shared:simulator:services/ml-inference:services/ml-training${PYTHONPATH:+:$PYTHONPATH}" \
    python -m ml_training.training_scripts.train_edge_autoencoder --output-dir "$OUTPUT_DIR"

echo ""
echo "Model + metadata saved to: $OUTPUT_DIR"
