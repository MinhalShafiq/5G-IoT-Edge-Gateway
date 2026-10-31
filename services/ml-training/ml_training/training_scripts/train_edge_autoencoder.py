"""Train the edge anomaly-detection autoencoder and export it to ONNX.

This is the model the ml-inference service serves. The pipeline:

1. Generate labelled telemetry with the simulator's own device classes
   (the same code that produces live traffic), with a 5% anomaly rate.
2. Learn per-device-type normalisation statistics from the normal readings.
3. Build features with ml-inference's ``FeatureExtractor`` -- the exact code
   used at serving time, so training and serving cannot drift apart.
4. Train a PyTorch ``Autoencoder`` on normal readings only.
5. Calibrate the reconstruction-error threshold that maximises F1 on a
   held-out labelled split, and report precision/recall per device type.
6. Export a graph that outputs the per-sample reconstruction error, verify
   ONNX Runtime matches PyTorch, and write the ``.json`` metadata sidecar.

Usage:
    python -m ml_training.training_scripts.train_edge_autoencoder --output-dir model_store
"""

from __future__ import annotations

import argparse
import json
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
import torch.nn as nn

from ml_inference.services.feature_extractor import (
    FEATURE_NAMES,
    N_FEATURES,
    SENSOR_FIELDS,
    FeatureExtractor,
)
from ml_training.architectures.autoencoder import Autoencoder
from shared.models.telemetry import TelemetryReading
from simulator.devices.pressure_sensor import PressureSensor
from simulator.devices.temperature_sensor import TemperatureSensor
from simulator.devices.vibration_sensor import VibrationSensor

MODEL_NAME = "anomaly-autoencoder"
_DEVICE_CLASSES = (TemperatureSensor, PressureSensor, VibrationSensor)


class ReconstructionErrorModel(nn.Module):
    """Wraps the autoencoder so the exported graph returns MSE per sample.

    The reconstruction is clamped to the per-feature range seen in normal
    training data. A ReLU autoencoder can otherwise learn to extrapolate
    linearly and reconstruct an extreme input (e.g. z=+20) almost perfectly,
    hiding exactly the anomalies it should flag.
    """

    def __init__(self, autoencoder: Autoencoder, lower: np.ndarray, upper: np.ndarray) -> None:
        super().__init__()
        self.autoencoder = autoencoder
        self.register_buffer("lower", torch.as_tensor(lower, dtype=torch.float32))
        self.register_buffer("upper", torch.as_tensor(upper, dtype=torch.float32))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        reconstruction = torch.clamp(self.autoencoder(x), self.lower, self.upper)
        return torch.mean((x - reconstruction) ** 2, dim=1)


def generate_telemetry(
    devices_per_type: int, steps: int, anomaly_rate: float
) -> list[tuple[TelemetryReading, bool]]:
    """Simulate a fleet, interleaving devices per step like the live stream.

    Each device's full lifecycle is included, starting from an empty history
    window, because that is what a freshly started gateway sees.
    """
    devices = [
        cls(device_id=str(uuid.uuid4()))
        for cls in _DEVICE_CLASSES
        for _ in range(devices_per_type)
    ]
    readings = []
    for _ in range(steps):
        for device in devices:
            is_anomaly = random.random() < anomaly_rate
            msg = device.get_telemetry(anomaly=is_anomaly)
            readings.append((TelemetryReading(**msg), is_anomaly))
    return readings


def split_by_device(
    readings: list[tuple[TelemetryReading, bool]], fractions: tuple[float, float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Boolean masks assigning whole devices to train / calibration / test.

    Splitting by device (not by time) keeps every split's mix of warm-up and
    steady-state readings identical to the live stream.
    """
    device_ids = sorted({str(r.device_id) for r, _ in readings})
    random.shuffle(device_ids)
    n_train = int(len(device_ids) * fractions[0])
    n_cal = int(len(device_ids) * fractions[1])
    split_of = {d: 0 for d in device_ids[:n_train]}
    split_of.update({d: 1 for d in device_ids[n_train : n_train + n_cal]})
    split = np.asarray([split_of.get(str(r.device_id), 2) for r, _ in readings])
    return split == 0, split == 1, split == 2


def compute_feature_stats(
    readings: list[tuple[TelemetryReading, bool]],
) -> dict[str, dict[str, list[float]]]:
    """Per-type mean/std of each sensor field, from normal readings only."""
    stats: dict[str, dict[str, list[float]]] = {}
    for device_type, fields in SENSOR_FIELDS.items():
        stats[device_type] = {}
        for field in fields:
            values = np.array(
                [
                    r.payload[field]
                    for r, is_anomaly in readings
                    if r.device_type == device_type and not is_anomaly
                ]
            )
            stats[device_type][field] = [float(values.mean()), float(values.std())]
    return stats


def extract_features(
    readings: list[tuple[TelemetryReading, bool]],
    feature_stats: dict,
    window_size: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    extractor = FeatureExtractor(window_size=window_size, feature_stats=feature_stats)
    x = np.asarray([extractor.extract_row(r) for r, _ in readings], dtype=np.float32)
    y = np.asarray([is_anomaly for _, is_anomaly in readings], dtype=bool)
    types = np.asarray([r.device_type for r, _ in readings])
    return x, y, types


def train(
    model: Autoencoder, x_train: np.ndarray, x_val: np.ndarray, epochs: int, lr: float
) -> list[dict]:
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()
    train_t = torch.from_numpy(x_train)
    val_t = torch.from_numpy(x_val)
    history = []

    for epoch in range(1, epochs + 1):
        model.train()
        perm = torch.randperm(len(train_t))
        total = 0.0
        for i in range(0, len(train_t), 256):
            batch = train_t[perm[i : i + 256]]
            optimizer.zero_grad()
            loss = loss_fn(model(batch), batch)
            loss.backward()
            optimizer.step()
            total += loss.item() * len(batch)

        model.eval()
        with torch.no_grad():
            val_loss = loss_fn(model(val_t), val_t).item()
        history.append(
            {"epoch": epoch, "train_loss": total / len(train_t), "val_loss": val_loss}
        )
        if epoch == 1 or epoch % 10 == 0 or epoch == epochs:
            print(
                f"  epoch {epoch:3d}  train_loss={history[-1]['train_loss']:.5f}"
                f"  val_loss={val_loss:.5f}"
            )
    return history


def precision_recall(pred: np.ndarray, truth: np.ndarray) -> dict:
    tp = int((pred & truth).sum())
    fp = int((pred & ~truth).sum())
    fn = int((~pred & truth).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
    }


def calibrate_threshold(errors: np.ndarray, truth: np.ndarray) -> float:
    """Pick the error threshold that maximises F1 on labelled data."""
    candidates = np.quantile(errors, np.linspace(0.80, 0.999, 400))
    return float(
        max(candidates, key=lambda t: precision_recall(errors > t, truth)["f1"])
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--output-dir", default="model_store")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--devices-per-type", type=int, default=500)
    parser.add_argument("--steps", type=int, default=60)
    parser.add_argument("--anomaly-rate", type=float, default=0.05)
    parser.add_argument("--window-size", type=int, default=10)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    print("Generating telemetry with the simulator's device models...")
    readings = generate_telemetry(args.devices_per_type, args.steps, args.anomaly_rate)
    feature_stats = compute_feature_stats(readings)
    x, y, types = extract_features(readings, feature_stats, args.window_size)
    print(f"  {len(x)} readings, {int(y.sum())} anomalies, {N_FEATURES} features")

    # 60% of devices train, 20% calibrate the threshold, 20% test.
    train_mask, cal_mask, test_mask = split_by_device(readings, (0.6, 0.2))
    x_train = x[train_mask & ~y]  # train on normal readings only
    x_cal, y_cal = x[cal_mask], y[cal_mask]
    x_test, y_test, types_test = x[test_mask], y[test_mask], types[test_mask]

    print("Training PyTorch autoencoder...")
    autoencoder = Autoencoder(input_dim=N_FEATURES, encoding_dim=4)
    history = train(autoencoder, x_train, x_cal[~y_cal], args.epochs, args.lr)

    scorer = ReconstructionErrorModel(
        autoencoder, x_train.min(axis=0), x_train.max(axis=0)
    ).eval()
    with torch.no_grad():
        cal_errors = scorer(torch.from_numpy(x_cal)).numpy()
        test_errors = scorer(torch.from_numpy(x_test)).numpy()

    threshold = calibrate_threshold(cal_errors, y_cal)
    test_pred = test_errors > threshold
    metrics = {"overall": precision_recall(test_pred, y_test)}
    for device_type in SENSOR_FIELDS:
        mask = types_test == device_type
        metrics[device_type] = precision_recall(test_pred[mask], y_test[mask])

    print(f"Calibrated error threshold: {threshold:.5f}")
    print("Held-out test metrics:")
    for name, m in metrics.items():
        print(
            f"  {name:20s} precision={m['precision']:.3f} "
            f"recall={m['recall']:.3f} f1={m['f1']:.3f}"
        )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / f"{MODEL_NAME}-v{args.version}.onnx"

    torch.onnx.export(
        scorer,
        torch.from_numpy(x_test[:1]),
        str(onnx_path),
        export_params=True,
        opset_version=17,
        do_constant_folding=True,
        input_names=["features"],
        output_names=["reconstruction_error"],
        dynamic_axes={
            "features": {0: "batch_size"},
            "reconstruction_error": {0: "batch_size"},
        },
        dynamo=False,
    )

    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    (ort_errors,) = session.run(None, {"features": x_test})
    rel_diff = float(
        (np.abs(ort_errors - test_errors) / np.maximum(np.abs(test_errors), 1e-3)).max()
    )
    if rel_diff > 1e-3:
        raise SystemExit(f"ONNX Runtime output diverges from PyTorch (max rel diff {rel_diff})")
    print(f"ONNX Runtime parity check passed (max relative diff {rel_diff:.2e})")

    metadata = {
        "model_name": MODEL_NAME,
        "model_version": args.version,
        "framework": "pytorch",
        "architecture": "autoencoder",
        "exported_with": f"torch {torch.__version__}",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "input_name": "features",
        "feature_names": list(FEATURE_NAMES),
        "feature_stats": feature_stats,
        "window_size": args.window_size,
        "error_threshold": threshold,
        "test_metrics": metrics,
        "training": {
            "samples": len(x_train),
            "epochs": args.epochs,
            "final_val_loss": history[-1]["val_loss"],
            "anomaly_rate": args.anomaly_rate,
            "seed": args.seed,
        },
    }
    onnx_path.with_suffix(".json").write_text(json.dumps(metadata, indent=2))
    print(f"Wrote {onnx_path} and {onnx_path.with_suffix('.json')}")


if __name__ == "__main__":
    main()
