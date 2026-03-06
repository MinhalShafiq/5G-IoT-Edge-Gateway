"""Experiments router — browse training experiments and per-epoch metrics."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/experiments", tags=["experiments"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class EpochMetric(BaseModel):
    """Single metric observation for one epoch."""

    epoch: int
    metric_name: str
    value: float


class ExperimentSummary(BaseModel):
    """Abbreviated experiment info for list endpoints."""

    job_id: str
    model_type: str
    config: dict[str, Any]
    status: str
    best_train_loss: float | None = None
    best_val_loss: float | None = None
    created_at: datetime | None = None


class ExperimentMetrics(BaseModel):
    """Detailed per-epoch metrics for an experiment."""

    job_id: str
    model_type: str
    metrics: list[EpochMetric]
    total_epochs_logged: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ExperimentSummary])
async def list_experiments(request: Request) -> list[ExperimentSummary]:
    """List all tracked experiments with summary metrics."""
    tracker = request.app.state.experiment_tracker
    experiments = tracker.list_experiments()

    results: list[ExperimentSummary] = []
    for exp in experiments:
        # Compute best losses from recorded metrics
        train_losses = [
            m["value"] for m in exp.get("metrics", []) if m["metric_name"] == "train_loss"
        ]
        val_losses = [
            m["value"] for m in exp.get("metrics", []) if m["metric_name"] == "val_loss"
        ]

        results.append(
            ExperimentSummary(
                job_id=exp["job_id"],
                model_type=exp["model_type"],
                config=exp.get("config", {}),
                status=exp.get("status", "unknown"),
                best_train_loss=min(train_losses) if train_losses else None,
                best_val_loss=min(val_losses) if val_losses else None,
                created_at=exp.get("created_at"),
            )
        )

    return results


@router.get("/{experiment_id}/metrics", response_model=ExperimentMetrics)
async def get_experiment_metrics(
    experiment_id: str, request: Request
) -> ExperimentMetrics:
    """Get detailed per-epoch training metrics for an experiment."""
    tracker = request.app.state.experiment_tracker
    exp = tracker.get_experiment(experiment_id)

    if exp is None:
        raise HTTPException(
            status_code=404,
            detail=f"Experiment {experiment_id} not found",
        )

    raw_metrics = exp.get("metrics", [])
    epoch_metrics = [
        EpochMetric(
            epoch=m["epoch"],
            metric_name=m["metric_name"],
            value=m["value"],
        )
        for m in raw_metrics
    ]

    return ExperimentMetrics(
        job_id=exp["job_id"],
        model_type=exp["model_type"],
        metrics=epoch_metrics,
        total_epochs_logged=len({m["epoch"] for m in raw_metrics}),
    )
