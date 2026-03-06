"""Experiment tracker — in-memory tracking of training experiments.

Records per-epoch metrics, hyperparameters, and final results for each
training job so they can be browsed via the experiments API.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class ExperimentTracker:
    """Track training experiments and their metrics.

    Thread-safe: the training loop runs in a background thread and logs
    metrics concurrently with API reads.
    """

    def __init__(self) -> None:
        self._experiments: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create_experiment(
        self,
        job_id: str,
        model_type: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a new experiment entry.

        Args:
            job_id: Unique training job identifier.
            model_type: Architecture name (e.g. "autoencoder").
            config: Hyperparameter dict (epochs, lr, batch_size, etc.).

        Returns:
            The newly created experiment dict.
        """
        experiment = {
            "job_id": job_id,
            "model_type": model_type,
            "config": config,
            "status": "pending",
            "metrics": [],
            "created_at": datetime.now(timezone.utc),
        }

        with self._lock:
            self._experiments[job_id] = experiment

        logger.info("experiment created", job_id=job_id, model_type=model_type)
        return experiment

    def log_metric(
        self,
        job_id: str,
        epoch: int,
        metric_name: str,
        value: float,
    ) -> None:
        """Record a metric observation for a training epoch.

        Args:
            job_id: Training job identifier.
            epoch: Epoch number (1-indexed).
            metric_name: Metric key (e.g. "train_loss", "val_loss").
            value: Metric value.
        """
        with self._lock:
            experiment = self._experiments.get(job_id)
            if experiment is None:
                logger.warning(
                    "metric logged for unknown experiment", job_id=job_id
                )
                return

            experiment["metrics"].append(
                {
                    "epoch": epoch,
                    "metric_name": metric_name,
                    "value": value,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

    def update_status(self, job_id: str, status: str) -> None:
        """Update the status of an experiment.

        Args:
            job_id: Training job identifier.
            status: New status string (running, completed, failed, cancelled).
        """
        with self._lock:
            experiment = self._experiments.get(job_id)
            if experiment is not None:
                experiment["status"] = status

    def get_experiment(self, job_id: str) -> dict[str, Any] | None:
        """Return a copy of the experiment dict, or None if not found."""
        with self._lock:
            experiment = self._experiments.get(job_id)
            if experiment is None:
                return None
            # Return a shallow copy to avoid mutations outside the lock
            return {**experiment, "metrics": list(experiment["metrics"])}

    def list_experiments(self) -> list[dict[str, Any]]:
        """Return all experiments, newest first."""
        with self._lock:
            experiments = [
                {**exp, "metrics": list(exp["metrics"])}
                for exp in self._experiments.values()
            ]

        experiments.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        return experiments
