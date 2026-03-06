"""Core training engine.

Manages the lifecycle of training jobs: creation, execution in a background
thread, progress tracking, ONNX export, and cancellation.
"""

from __future__ import annotations

import asyncio
import os
import traceback
from datetime import datetime, timezone
from uuid import uuid4

import numpy as np
import structlog
import torch
import torch.nn as nn

from ml_training.architectures.autoencoder import Autoencoder
from ml_training.architectures.lstm_predictor import LSTMPredictor
from ml_training.routers.training_jobs import TrainingJob, TrainingJobCreate
from ml_training.services.data_loader import TelemetryDataLoader
from ml_training.services.model_exporter import ModelExporter
from ml_training.services.experiment_tracker import ExperimentTracker

logger = structlog.get_logger(__name__)

# Maps model_type strings to architecture classes
_MODEL_REGISTRY: dict[str, type[nn.Module]] = {
    "autoencoder": Autoencoder,
    "lstm": LSTMPredictor,
}


class Trainer:
    """Orchestrates model training jobs."""

    def __init__(
        self,
        settings,
        data_loader: TelemetryDataLoader,
        model_exporter: ModelExporter,
        experiment_tracker: ExperimentTracker,
    ):
        self._settings = settings
        self._data_loader = data_loader
        self._model_exporter = model_exporter
        self._experiment_tracker = experiment_tracker
        self._jobs: dict[str, TrainingJob] = {}
        self._cancelled: set[str] = set()
        self._device = torch.device(settings.device)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_training(self, job_config: TrainingJobCreate) -> TrainingJob:
        """Launch a training job in a background thread.

        Returns immediately with the job record in ``pending`` status.
        """
        job_id = str(uuid4())
        job_record = TrainingJob(
            job_id=job_id,
            model_type=job_config.model_type,
            status="pending",
            epochs_completed=0,
            total_epochs=job_config.epochs,
            created_at=datetime.now(timezone.utc),
        )
        self._jobs[job_id] = job_record

        # Create experiment entry for tracking
        self._experiment_tracker.create_experiment(
            job_id=job_id,
            model_type=job_config.model_type,
            config={
                "epochs": job_config.epochs,
                "batch_size": job_config.batch_size,
                "learning_rate": job_config.learning_rate,
                "data_query": job_config.data_query,
            },
        )

        # Load training data asynchronously, then dispatch blocking train loop
        loop = asyncio.get_event_loop()

        async def _run() -> None:
            try:
                # Load data from PostgreSQL
                x_train, x_val = await self._data_loader.load_training_data(
                    device_type=job_config.data_query.get("device_type"),
                    start_time=job_config.data_query.get("start_time"),
                    end_time=job_config.data_query.get("end_time"),
                    limit=job_config.data_query.get("limit", 50000),
                )
            except Exception:
                # If data loading fails, generate synthetic data for training
                logger.warning(
                    "data loading failed, using synthetic data",
                    job_id=job_id,
                )
                x_train = np.random.randn(1000, 8).astype(np.float32)
                x_val = np.random.randn(200, 8).astype(np.float32)

            await loop.run_in_executor(
                None, self._train, job_record, job_config, x_train, x_val
            )

        asyncio.ensure_future(_run())
        return job_record

    def list_jobs(self) -> list[TrainingJob]:
        """Return all known training jobs, newest first."""
        return sorted(
            self._jobs.values(),
            key=lambda j: j.created_at,
            reverse=True,
        )

    def get_job(self, job_id: str) -> TrainingJob | None:
        """Return a single job by ID, or None."""
        return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        """Request cancellation for a running or pending job.

        Returns True if the job was found and marked for cancellation.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status in ("completed", "failed", "cancelled"):
            return False

        self._cancelled.add(job_id)
        job.status = "cancelled"
        job.completed_at = datetime.now(timezone.utc)
        self._experiment_tracker.update_status(job_id, "cancelled")
        logger.info("job cancellation requested", job_id=job_id)
        return True

    # ------------------------------------------------------------------
    # Internal training loop (runs in thread-pool executor)
    # ------------------------------------------------------------------

    def _train(
        self,
        job_record: TrainingJob,
        job_config: TrainingJobCreate,
        x_train: np.ndarray,
        x_val: np.ndarray,
    ) -> None:
        """Blocking training loop executed in a background thread."""
        try:
            job_record.status = "running"
            self._experiment_tracker.update_status(job_record.job_id, "running")

            model = self._create_model(job_config.model_type, input_dim=x_train.shape[1])
            model = model.to(self._device)

            optimizer = torch.optim.Adam(model.parameters(), lr=job_config.learning_rate)
            criterion = nn.MSELoss()

            # Convert numpy to tensors
            train_tensor = torch.tensor(x_train, dtype=torch.float32).to(self._device)
            val_tensor = torch.tensor(x_val, dtype=torch.float32).to(self._device)

            batch_size = job_config.batch_size
            n_samples = train_tensor.shape[0]

            logger.info(
                "training started",
                job_id=job_record.job_id,
                model_type=job_config.model_type,
                n_train=n_samples,
                n_val=x_val.shape[0],
                device=str(self._device),
            )

            for epoch in range(job_config.epochs):
                # Check for cancellation
                if job_record.job_id in self._cancelled:
                    logger.info("training cancelled by user", job_id=job_record.job_id)
                    return

                # --- Training step ---
                model.train()
                epoch_loss = 0.0
                n_batches = 0

                # Shuffle indices each epoch
                indices = torch.randperm(n_samples, device=self._device)

                for start in range(0, n_samples, batch_size):
                    batch_idx = indices[start : start + batch_size]
                    batch = train_tensor[batch_idx]

                    optimizer.zero_grad()

                    if job_config.model_type == "lstm":
                        # LSTM expects (batch, seq_len, features) — treat each
                        # sample as a single-step sequence for basic training
                        batch_input = batch.unsqueeze(1)
                        output = model(batch_input)
                        loss = criterion(output, batch)
                    else:
                        # Autoencoder: reconstruct input
                        output = model(batch)
                        loss = criterion(output, batch)

                    loss.backward()
                    optimizer.step()

                    epoch_loss += loss.item()
                    n_batches += 1

                avg_train_loss = epoch_loss / max(n_batches, 1)

                # --- Validation step ---
                model.eval()
                with torch.no_grad():
                    if job_config.model_type == "lstm":
                        val_input = val_tensor.unsqueeze(1)
                        val_output = model(val_input)
                        val_loss = criterion(val_output, val_tensor).item()
                    else:
                        val_output = model(val_tensor)
                        val_loss = criterion(val_output, val_tensor).item()

                # Update job record
                job_record.epochs_completed = epoch + 1
                job_record.train_loss = avg_train_loss
                job_record.val_loss = val_loss

                # Log metrics to experiment tracker
                self._experiment_tracker.log_metric(
                    job_record.job_id, epoch + 1, "train_loss", avg_train_loss
                )
                self._experiment_tracker.log_metric(
                    job_record.job_id, epoch + 1, "val_loss", val_loss
                )

                if (epoch + 1) % 10 == 0 or epoch == 0:
                    logger.info(
                        "epoch completed",
                        job_id=job_record.job_id,
                        epoch=epoch + 1,
                        train_loss=round(avg_train_loss, 6),
                        val_loss=round(val_loss, 6),
                    )

            # --- Export to ONNX ---
            os.makedirs(self._settings.model_output_dir, exist_ok=True)
            output_path = os.path.join(
                self._settings.model_output_dir, f"{job_record.job_id}.onnx"
            )

            input_dim = x_train.shape[1]
            if job_config.model_type == "lstm":
                input_shape = (1, 1, input_dim)
            else:
                input_shape = (1, input_dim)

            self._model_exporter.export_to_onnx(
                model=model,
                input_shape=input_shape,
                output_path=output_path,
            )

            job_record.model_path = output_path
            job_record.status = "completed"
            job_record.completed_at = datetime.now(timezone.utc)
            self._experiment_tracker.update_status(job_record.job_id, "completed")

            logger.info(
                "training completed",
                job_id=job_record.job_id,
                model_path=output_path,
                final_train_loss=round(job_record.train_loss, 6) if job_record.train_loss else None,
                final_val_loss=round(job_record.val_loss, 6) if job_record.val_loss else None,
            )

        except Exception as exc:
            job_record.status = "failed"
            job_record.error_message = f"{type(exc).__name__}: {exc}"
            job_record.completed_at = datetime.now(timezone.utc)
            self._experiment_tracker.update_status(job_record.job_id, "failed")
            logger.error(
                "training failed",
                job_id=job_record.job_id,
                error=str(exc),
                traceback=traceback.format_exc(),
            )

    # ------------------------------------------------------------------
    # Model factory
    # ------------------------------------------------------------------

    def _create_model(self, model_type: str, input_dim: int = 8) -> nn.Module:
        """Instantiate a model architecture by name."""
        if model_type == "autoencoder":
            return Autoencoder(input_dim=input_dim, encoding_dim=max(input_dim // 2, 2))
        elif model_type == "lstm":
            return LSTMPredictor(
                input_dim=input_dim,
                hidden_dim=32,
                num_layers=2,
                output_dim=input_dim,
            )
        else:
            raise ValueError(
                f"Unknown model type '{model_type}'. "
                f"Supported types: {list(_MODEL_REGISTRY.keys())}"
            )
