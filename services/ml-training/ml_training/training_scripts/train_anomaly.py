"""CLI script for training anomaly detection models.

Usage:
    python -m ml_training.training_scripts.train_anomaly --epochs 50 --model autoencoder
    python -m ml_training.training_scripts.train_anomaly --model lstm --epochs 100 --lr 0.0005
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

import numpy as np
import structlog
import torch
import torch.nn as nn

from ml_training.architectures.autoencoder import Autoencoder
from ml_training.architectures.lstm_predictor import LSTMPredictor
from ml_training.config import Settings
from ml_training.services.data_loader import TelemetryDataLoader
from ml_training.services.model_exporter import ModelExporter

# Configure basic structured logging for CLI
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
)

logger = structlog.get_logger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Train an anomaly detection model on IoT sensor data"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="autoencoder",
        choices=["autoencoder", "lstm"],
        help="Model architecture to train (default: autoencoder)",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=50,
        help="Number of training epochs (default: 50)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Training batch size (default: 64)",
    )
    parser.add_argument(
        "--lr",
        type=float,
        default=0.001,
        help="Learning rate (default: 0.001)",
    )
    parser.add_argument(
        "--device-type",
        type=str,
        default=None,
        help="Filter training data by device type",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Model output directory (default: from settings)",
    )
    parser.add_argument(
        "--register",
        action="store_true",
        help="Register the model with the cloud model registry after training",
    )
    parser.add_argument(
        "--version",
        type=str,
        default="1.0.0",
        help="Model version string for registry (default: 1.0.0)",
    )
    return parser.parse_args()


def create_model(model_type: str, input_dim: int) -> nn.Module:
    """Instantiate a model architecture."""
    if model_type == "autoencoder":
        return Autoencoder(input_dim=input_dim, encoding_dim=max(input_dim // 2, 2))
    elif model_type == "lstm":
        return LSTMPredictor(
            input_dim=input_dim, hidden_dim=32, num_layers=2, output_dim=input_dim
        )
    else:
        raise ValueError(f"Unknown model type: {model_type}")


async def load_data(
    settings: Settings, device_type: str | None
) -> tuple[np.ndarray, np.ndarray]:
    """Attempt to load data from PostgreSQL, fall back to synthetic data."""
    loader = TelemetryDataLoader(settings.postgres_dsn)
    try:
        x_train, x_val = await loader.load_training_data(device_type=device_type)
        logger.info("loaded real data", n_train=len(x_train), n_val=len(x_val))
        return x_train, x_val
    except Exception as exc:
        logger.warning(
            "could not load data from PostgreSQL, using synthetic data",
            error=str(exc),
        )
        x_train = np.random.randn(2000, 8).astype(np.float32)
        x_val = np.random.randn(400, 8).astype(np.float32)
        return x_train, x_val


def train(
    model: nn.Module,
    x_train: np.ndarray,
    x_val: np.ndarray,
    args: argparse.Namespace,
    device: torch.device,
) -> tuple[nn.Module, dict]:
    """Run the training loop.

    Returns:
        Tuple of (trained model, metrics dict).
    """
    model = model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    criterion = nn.MSELoss()

    train_tensor = torch.tensor(x_train, dtype=torch.float32).to(device)
    val_tensor = torch.tensor(x_val, dtype=torch.float32).to(device)

    n_samples = train_tensor.shape[0]
    is_lstm = args.model == "lstm"

    best_val_loss = float("inf")
    history: dict = {"train_loss": [], "val_loss": []}

    start_time = time.time()

    for epoch in range(args.epochs):
        # --- Training ---
        model.train()
        epoch_loss = 0.0
        n_batches = 0
        indices = torch.randperm(n_samples, device=device)

        for start in range(0, n_samples, args.batch_size):
            batch_idx = indices[start : start + args.batch_size]
            batch = train_tensor[batch_idx]

            optimizer.zero_grad()

            if is_lstm:
                output = model(batch.unsqueeze(1))
            else:
                output = model(batch)

            loss = criterion(output, batch)
            loss.backward()
            optimizer.step()

            epoch_loss += loss.item()
            n_batches += 1

        avg_train_loss = epoch_loss / max(n_batches, 1)

        # --- Validation ---
        model.eval()
        with torch.no_grad():
            if is_lstm:
                val_output = model(val_tensor.unsqueeze(1))
            else:
                val_output = model(val_tensor)
            val_loss = criterion(val_output, val_tensor).item()

        history["train_loss"].append(avg_train_loss)
        history["val_loss"].append(val_loss)

        if val_loss < best_val_loss:
            best_val_loss = val_loss

        if (epoch + 1) % 10 == 0 or epoch == 0 or epoch == args.epochs - 1:
            logger.info(
                "epoch",
                epoch=epoch + 1,
                train_loss=round(avg_train_loss, 6),
                val_loss=round(val_loss, 6),
                best_val=round(best_val_loss, 6),
            )

    elapsed = time.time() - start_time

    metrics = {
        "final_train_loss": history["train_loss"][-1],
        "final_val_loss": history["val_loss"][-1],
        "best_val_loss": best_val_loss,
        "total_epochs": args.epochs,
        "training_time_seconds": round(elapsed, 2),
    }

    logger.info("training complete", **metrics)
    return model, metrics


async def main() -> None:
    """Main entry point: load data, train, export, optionally register."""
    args = parse_args()
    settings = Settings()

    output_dir = args.output_dir or settings.model_output_dir
    device = torch.device(settings.device)

    logger.info(
        "starting anomaly model training",
        model=args.model,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        device=str(device),
    )

    # Load data
    x_train, x_val = await load_data(settings, args.device_type)

    # Create and train model
    input_dim = x_train.shape[1]
    model = create_model(args.model, input_dim)
    model, metrics = train(model, x_train, x_val, args, device)

    # Export to ONNX
    os.makedirs(output_dir, exist_ok=True)
    model_name = f"anomaly-{args.model}"
    output_path = os.path.join(output_dir, f"{model_name}-v{args.version}.onnx")

    exporter = ModelExporter(
        registry_url=settings.model_registry_url,
        output_dir=output_dir,
    )

    if args.model == "lstm":
        input_shape = (1, 1, input_dim)
    else:
        input_shape = (1, input_dim)

    exporter.export_to_onnx(model, input_shape, output_path)
    logger.info("model exported", path=output_path)

    # Optionally register with the model registry
    if args.register:
        logger.info("registering model with registry")
        result = await exporter.register_model(
            name=model_name,
            version=args.version,
            framework="onnx",
            file_path=output_path,
            metrics=metrics,
            description=f"Anomaly detection {args.model} trained on IoT sensor data",
        )
        if result:
            logger.info("model registered successfully", result=result)
        else:
            logger.error("model registration failed")

    logger.info("done")


if __name__ == "__main__":
    asyncio.run(main())
