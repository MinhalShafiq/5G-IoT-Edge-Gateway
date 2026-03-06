"""Job execution engine.

Manages the lifecycle of Spark batch jobs: submission, execution in
background threads, status tracking, and cancellation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import structlog

from batch_analytics.routers.jobs import SparkJob, SparkJobCreate
from batch_analytics.services.spark_manager import SparkManager
from batch_analytics.services.result_store import ResultStore
from batch_analytics.spark_jobs import (
    sensor_aggregation,
    anomaly_report,
    device_health,
    trend_analysis,
)

logger = structlog.get_logger(__name__)


class JobRunner:
    """Submits and executes Spark jobs in background threads."""

    def __init__(self, spark_manager: SparkManager, result_store: ResultStore) -> None:
        self._spark_manager = spark_manager
        self._result_store = result_store
        self._jobs: dict[str, SparkJob] = {}

    async def submit_job(self, job_create: SparkJobCreate) -> SparkJob:
        """Submit a Spark job and run it in a background thread.

        The job is created with status ``pending``, then dispatched to
        a thread-pool executor so the blocking Spark work does not block
        the async event loop.

        Returns:
            The newly created SparkJob with its assigned job_id.
        """
        job = SparkJob(
            job_id=str(uuid4()),
            job_type=job_create.job_type,
            status="pending",
            params=job_create.params,
            created_at=datetime.now(timezone.utc),
        )
        self._jobs[job.job_id] = job

        logger.info(
            "job submitted",
            job_id=job.job_id,
            job_type=job.job_type,
            params=job.params,
        )

        # Run the blocking Spark job in the default thread-pool executor
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, self._execute_job, job)

        return job

    def _execute_job(self, job: SparkJob) -> None:
        """Execute a Spark job synchronously (runs in a thread).

        Dispatches to the appropriate spark_jobs module based on job_type,
        captures the result summary, and updates the job status.
        """
        job.status = "running"
        logger.info("job started", job_id=job.job_id, job_type=job.job_type)

        try:
            match job.job_type:
                case "sensor_aggregation":
                    result = sensor_aggregation.run(
                        self._spark_manager, job.params
                    )
                case "anomaly_report":
                    result = anomaly_report.run(
                        self._spark_manager, job.params
                    )
                case "device_health":
                    result = device_health.run(
                        self._spark_manager, job.params
                    )
                case "trend_analysis":
                    result = trend_analysis.run(
                        self._spark_manager, job.params
                    )
                case _:
                    raise ValueError(f"Unknown job type: {job.job_type}")

            # Check if the job was cancelled while running
            if job.status == "cancelled":
                logger.info("job was cancelled during execution", job_id=job.job_id)
                return

            # Store full results and update job metadata
            rows_processed = result.get("rows_processed", 0)
            summary = result.get("summary", result)

            self._result_store.store(job.job_id, result)

            job.result_summary = summary
            job.rows_processed = rows_processed
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)

            logger.info(
                "job completed",
                job_id=job.job_id,
                rows_processed=rows_processed,
            )

        except Exception as e:
            if job.status == "cancelled":
                logger.info("cancelled job encountered error", job_id=job.job_id)
                return

            job.status = "failed"
            job.error_message = str(e)
            job.completed_at = datetime.now(timezone.utc)

            logger.error(
                "job failed",
                job_id=job.job_id,
                error=str(e),
                exc_info=True,
            )

    def list_jobs(self) -> list[SparkJob]:
        """Return all jobs sorted by creation time (newest first)."""
        return sorted(
            self._jobs.values(),
            key=lambda j: j.created_at,
            reverse=True,
        )

    def get_job(self, job_id: str) -> SparkJob | None:
        """Return a job by its ID, or None if not found."""
        return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> None:
        """Mark a job as cancelled.

        If the job is currently running in a thread, the cancellation is
        cooperative — the thread will check the status flag and stop
        at the next safe point.
        """
        job = self._jobs.get(job_id)
        if job is not None:
            job.status = "cancelled"
            job.completed_at = datetime.now(timezone.utc)
            logger.info("job cancelled", job_id=job_id)
