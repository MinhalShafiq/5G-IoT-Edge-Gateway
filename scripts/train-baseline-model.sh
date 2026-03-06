#!/usr/bin/env bash
# Train and export the baseline anomaly detection model (Isolation Forest -> ONNX).
# Usage: bash scripts/train-baseline-model.sh

set -euo pipefail

OUTPUT_DIR="services/ml-inference/ml_inference/model_store"
MODEL_FILE="$OUTPUT_DIR/anomaly_detector.onnx"

echo "=== Training Baseline Anomaly Detection Model ==="

# Ensure output directory exists
mkdir -p "$OUTPUT_DIR"

# Train and export
python -m ml_inference.models.isolation_forest \
    --output "$MODEL_FILE" \
    --samples 10000

echo ""
echo "Model saved to: $MODEL_FILE"
echo "Model size: $(du -h "$MODEL_FILE" | cut -f1)"
