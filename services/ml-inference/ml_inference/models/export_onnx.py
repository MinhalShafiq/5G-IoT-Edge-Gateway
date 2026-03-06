"""Convert a PyTorch model to ONNX format.

Utility module for exporting PyTorch-based anomaly detection models (such as
autoencoders or variational autoencoders) to ONNX so they can be served by
the ONNX Runtime inference engine.

Usage:
    python -m ml_inference.models.export_onnx --model autoencoder --input-shape 1,8 --output model.onnx
"""

import argparse

import torch
import numpy as np


def export_pytorch_to_onnx(
    model: torch.nn.Module,
    input_shape: tuple[int, ...],
    output_path: str,
    opset_version: int = 13,
) -> None:
    """Export a PyTorch model to ONNX format.

    Args:
        model: The PyTorch model to export.  Must be in eval mode.
        input_shape: Shape of the input tensor (e.g., (1, 8)).
        output_path: Filesystem path to write the .onnx file.
        opset_version: ONNX opset version to target.
    """
    model.eval()
    dummy_input = torch.randn(*input_shape)

    torch.onnx.export(
        model,
        dummy_input,
        output_path,
        export_params=True,
        opset_version=opset_version,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={
            "input": {0: "batch_size"},
            "output": {0: "batch_size"},
        },
    )

    print(f"Model exported to {output_path}")
    print(f"  Input shape: {input_shape}")
    print(f"  Opset version: {opset_version}")


def verify_onnx_model(onnx_path: str, input_shape: tuple[int, ...]) -> None:
    """Verify an exported ONNX model by running a test inference.

    Args:
        onnx_path: Path to the .onnx file to verify.
        input_shape: Shape of the input tensor to test with.
    """
    import onnxruntime as ort

    session = ort.InferenceSession(
        onnx_path, providers=["CPUExecutionProvider"]
    )

    input_name = session.get_inputs()[0].name
    test_input = np.random.randn(*input_shape).astype(np.float32)
    results = session.run(None, {input_name: test_input})

    print(f"Verification passed:")
    print(f"  Input shape: {test_input.shape}")
    print(f"  Output shape: {results[0].shape}")
    print(f"  Output sample: {results[0][:5]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Export a PyTorch model to ONNX format"
    )
    parser.add_argument(
        "--model",
        default="autoencoder",
        help="Model architecture name (for reference only)",
    )
    parser.add_argument(
        "--input-shape",
        default="1,8",
        help="Comma-separated input tensor shape (e.g., 1,8)",
    )
    parser.add_argument(
        "--output",
        default="model.onnx",
        help="Output path for the ONNX model file",
    )
    parser.add_argument(
        "--opset",
        type=int,
        default=13,
        help="ONNX opset version",
    )
    args = parser.parse_args()

    input_shape = tuple(int(x) for x in args.input_shape.split(","))

    print(f"Export utility ready.")
    print(f"  Model: {args.model}")
    print(f"  Input shape: {input_shape}")
    print(f"  Output: {args.output}")
    print(f"  Opset: {args.opset}")
    print()
    print("To use this utility, import your PyTorch model and call:")
    print("  export_pytorch_to_onnx(model, input_shape, output_path)")
