"""Job results router.

Provides endpoints to retrieve full results and download CSV exports
for completed Spark batch jobs.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/results", tags=["results"])


@router.get("/{job_id}")
async def get_results(job_id: str, request: Request) -> dict:
    """Get the full results for a completed job.

    Returns the complete result data including all computed rows and metadata.
    """
    job_runner = request.app.state.job_runner
    result_store = request.app.state.result_store

    # Verify the job exists
    job = job_runner.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Job '{job_id}' is not completed (status: {job.status})",
        )

    results = result_store.get(job_id)
    if results is None:
        raise HTTPException(
            status_code=404, detail=f"Results for job '{job_id}' not found"
        )

    return {
        "job_id": job_id,
        "job_type": job.job_type,
        "rows_processed": job.rows_processed,
        "results": results,
    }


@router.get("/{job_id}/download")
async def download_results(job_id: str, request: Request) -> StreamingResponse:
    """Download the full results of a completed job as a CSV file.

    Returns a streaming CSV response suitable for large datasets.
    """
    job_runner = request.app.state.job_runner
    result_store = request.app.state.result_store

    # Verify the job exists
    job = job_runner.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Job '{job_id}' is not completed (status: {job.status})",
        )

    csv_content = result_store.to_csv(job_id)
    if csv_content is None:
        raise HTTPException(
            status_code=404, detail=f"Results for job '{job_id}' not found"
        )

    filename = f"{job.job_type}_{job_id}.csv"

    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
