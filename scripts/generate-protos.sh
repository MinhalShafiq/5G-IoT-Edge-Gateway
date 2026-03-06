#!/usr/bin/env bash
# Generate Python gRPC stubs from protobuf definitions.
# Usage: bash scripts/generate-protos.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PROTO_DIR="$PROJECT_ROOT/proto"
OUT_DIR="$PROJECT_ROOT/shared/shared/proto_gen"

echo "Generating Python protobuf and gRPC stubs..."
echo "Proto source: $PROTO_DIR"
echo "Output dir:   $OUT_DIR"

# Create output directory
mkdir -p "$OUT_DIR"

# Generate stubs for each proto file
for proto_file in "$PROTO_DIR"/gateway/v1/*.proto; do
    echo "  Compiling: $(basename "$proto_file")"
    python -m grpc_tools.protoc \
        -I "$PROTO_DIR" \
        --python_out="$OUT_DIR" \
        --pyi_out="$OUT_DIR" \
        --grpc_python_out="$OUT_DIR" \
        "$proto_file"
done

# Create __init__.py files
touch "$OUT_DIR/__init__.py"
mkdir -p "$OUT_DIR/gateway/v1"
touch "$OUT_DIR/gateway/__init__.py"
touch "$OUT_DIR/gateway/v1/__init__.py"

echo "Done. Generated stubs in $OUT_DIR"
