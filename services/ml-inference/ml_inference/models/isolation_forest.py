"""Train an IsolationForest anomaly detector and export to ONNX format.

Generates synthetic normal sensor data, trains a scikit-learn IsolationForest,
and exports the trained model to ONNX for use with the ONNX Runtime inference
engine in production.

Usage:
    python -m ml_inference.models.isolation_forest --output model_store/anomaly_detector.onnx
    python -m ml_inference.models.isolation_forest --output model_store/anomaly_detector.onnx --samples 10000
"""

import argparse

import numpy as np
from sklearn.ensemble import IsolationForest
from skl2onnx import to_onnx


def generate_training_data(
    n_samples: int = 5000, n_features: int = 8
) -> np.ndarray:
    """Generate synthetic normal sensor data for training.

    The synthetic data simulates typical IoT sensor readings with eight
    features representing: temperature, humidity, pressure, vibration,
    power_consumption, ambient_temp, noise_level, and barometric_delta.

    Args:
        n_samples: Number of training samples to generate.
        n_features: Number of features per sample.

    Returns:
        A numpy array of shape (n_samples, n_features).
    """
    rng = np.random.RandomState(42)
    # Centroids represent typical normal sensor readings
    centroids = np.array(
        [25.0, 50.0, 1013.0, 1.0, 100.0, 25.0, 0.5, 1013.0]
    )
    normal = rng.randn(n_samples, n_features) * 0.5 + centroids
    return normal


def train_and_export(
    output_path: str,
    n_samples: int = 5000,
    n_features: int = 8,
    contamination: float = 0.05,
) -> IsolationForest:
    """Train an IsolationForest model and export it to ONNX format.

    Args:
        output_path: Filesystem path to write the .onnx file.
        n_samples: Number of training samples.
        n_features: Number of features per sample.
        contamination: Expected proportion of outliers in the training set.

    Returns:
        The trained IsolationForest model.
    """
    data = generate_training_data(n_samples, n_features)

    model = IsolationForest(
        contamination=contamination,
        random_state=42,
        n_estimators=100,
    )
    model.fit(data)

    # Export to ONNX -- provide a sample input for shape inference
    onx = to_onnx(model, data[:1].astype(np.float32))

    with open(output_path, "wb") as f:
        f.write(onx.SerializeToString())

    print(f"Model exported to {output_path}")
    print(f"  Samples: {n_samples}, Features: {n_features}")
    print(f"  Contamination: {contamination}")
    print(f"  Estimators: 100")
    return model


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Train an IsolationForest and export to ONNX"
    )
    parser.add_argument(
        "--output",
        default="model_store/anomaly_detector.onnx",
        help="Output path for the ONNX model file",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=5000,
        help="Number of training samples",
    )
    parser.add_argument(
        "--features",
        type=int,
        default=8,
        help="Number of features per sample",
    )
    parser.add_argument(
        "--contamination",
        type=float,
        default=0.05,
        help="Expected proportion of outliers",
    )
    args = parser.parse_args()

    train_and_export(
        args.output,
        n_samples=args.samples,
        n_features=args.features,
        contamination=args.contamination,
    )
