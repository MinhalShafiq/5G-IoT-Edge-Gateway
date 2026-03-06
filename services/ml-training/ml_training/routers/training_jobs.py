"""Training jobs router — create, list, inspect, and cancel training jobs."""

from __future__ import annotations

from datetime import datetime

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["training-jobs"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class TrainingJobCreate(BaseModel):
    """Payload to start a new training job."""

    model_type: str = "autoencoder"  # or "isolation_forest", "lstm"
    epochs: int = 50
    batch_size: int = 64
    learning_rate: float = 0.001
    data_query: dict = Field(default_factory=dict)  # filter for training data


class TrainingJob(BaseModel):
    """Full training job representation returned by the API."""

    job_id: str
    model_type: str
    status: str  # pending, running, completed, failed, cancelled
    epochs_completed: int = 0
    total_epochs: int = 50
    train_loss: float | None = None
    val_loss: float | None = None
    model_path: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None


class TrainingJobSummary(BaseModel):
    """Abbreviated job info for list endpoints."""

    job_id: str
    model_type: str
    status: str
    epochs_completed: int
    total_epochs: int
    created_at: datetime


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=TrainingJob, status_code=201)
async def create_training_job(
    job_config: TrainingJobCreate,
    background_tasks: BackgroundTasks,
    request: Request,
) -> TrainingJob:
    """Start a new training job.

    The training runs as a background task so the endpoint returns immediately
    with the job_id and ``pending`` status.
    """
    trainer = request.app.state.trainer

    job_record = await trainer.start_training(job_config)

    logger.info(
        "training job created",
        job_id=job_record.job_id,
        model_type=job_config.model_type,
        epochs=job_config.epochs,
    )

    return job_record


@router.get("", response_model=list[TrainingJobSummary])
async def list_training_jobs(request: Request) -> list[TrainingJobSummary]:
    """List all training jobs with their current status."""
    trainer = request.app.state.trainer
    jobs = trainer.list_jobs()

    return [
        TrainingJobSummary(
            job_id=j.job_id,
            model_type=j.model_type,
            status=j.status,
            epochs_completed=j.epochs_completed,
            total_epochs=j.total_epochs,
            created_at=j.created_at,
        )
        for j in jobs
    ]


@router.get("/{job_id}", response_model=TrainingJob)
async def get_training_job(job_id: str, request: Request) -> TrainingJob:
    """Get detailed information and metrics for a specific training job."""
    trainer = request.app.state.trainer
    job = trainer.get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"Training job {job_id} not found")

    return job


@router.delete("/{job_id}", status_code=200)
async def cancel_training_job(job_id: str, request: Request) -> dict:
    """Cancel a running or pending training job."""
    trainer = request.app.state.trainer
    cancelled = trainer.cancel_job(job_id)

    if not cancelled:
        raise HTTPException(
            status_code=404,
            detail=f"Training job {job_id} not found or already completed",
        )

    logger.info("training job cancelled", job_id=job_id)
    return {"status": "cancelled", "job_id": job_id}
