"""Spark job management router.

Provides endpoints to submit, list, inspect, and cancel PySpark batch jobs
for historical sensor data analysis.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/jobs", tags=["jobs"])

VALID_JOB_TYPES = {
    "sensor_aggregation",
    "anomaly_report",
    "device_health",
    "trend_analysis",
}


class SparkJobCreate(BaseModel):
    """Request body for submitting a new Spark job."""

    job_type: str  # sensor_aggregation, anomaly_report, device_health, trend_analysis
    params: dict = {}  # start_time, end_time, device_type, etc.


class SparkJob(BaseModel):
    """Spark job status and metadata."""

    job_id: str
    job_type: str
    status: str  # pending, running, completed, failed, cancelled
    params: dict
    result_summary: dict | None = None
    created_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
    rows_processed: int = 0


@router.post("/", response_model=SparkJob, status_code=201)
async def submit_job(job_create: SparkJobCreate, request: Request) -> SparkJob:
    """Submit a new Spark batch job.

    Supported job types:
    - **sensor_aggregation**: Hourly/daily aggregation of sensor readings.
    - **anomaly_report**: Historical anomaly pattern analysis.
    - **device_health**: Fleet health scoring for all devices.
    - **trend_analysis**: Time-series trend detection with moving averages.
    """
    if job_create.job_type not in VALID_JOB_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid job_type '{job_create.job_type}'. "
                f"Valid types: {sorted(VALID_JOB_TYPES)}"
            ),
        )

    job_runner = request.app.state.job_runner
    job = await job_runner.submit_job(job_create)
    return job


@router.get("/", response_model=list[SparkJob])
async def list_jobs(request: Request) -> list[SparkJob]:
    """List all submitted jobs with their current status."""
    job_runner = request.app.state.job_runner
    return job_runner.list_jobs()


@router.get("/{job_id}", response_model=SparkJob)
async def get_job(job_id: str, request: Request) -> SparkJob:
    """Get the status and result summary of a specific job."""
    job_runner = request.app.state.job_runner
    job = job_runner.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


@router.delete("/{job_id}", status_code=204)
async def cancel_job(job_id: str, request: Request) -> None:
    """Cancel a running or pending job.

    Only jobs with status 'pending' or 'running' can be cancelled.
    Completed or failed jobs are unaffected.
    """
    job_runner = request.app.state.job_runner
    job = job_runner.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    if job.status not in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail=f"Job '{job_id}' cannot be cancelled (status: {job.status})",
        )

    job_runner.cancel_job(job_id)
