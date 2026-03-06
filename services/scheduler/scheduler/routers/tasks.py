"""Task submission and placement query endpoints.

Accepts ML inference tasks for placement, evaluates them against all registered
policies, and returns the optimal placement decision (edge local, edge remote,
or cloud).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from scheduler.services.scheduler_engine import InferenceTask, TaskPlacement

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/tasks", tags=["tasks"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class TaskSubmitResponse(BaseModel):
    """Response returned after submitting a task for placement."""

    task_id: str
    placement: TaskPlacement


class TaskListResponse(BaseModel):
    """Paginated list of recent task placements."""

    total: int
    tasks: list[TaskPlacement]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=TaskSubmitResponse, status_code=201)
async def submit_task(
    task: InferenceTask,
    request: Request,
) -> TaskSubmitResponse:
    """Submit an inference task for placement evaluation.

    The scheduler engine evaluates all registered policies and returns the
    placement with the highest score.
    """
    engine = request.app.state.scheduler_engine
    logger.info(
        "task submitted",
        task_id=task.task_id,
        model_name=task.model_name,
        priority=task.priority,
    )
    placement = await engine.submit_task(task)
    logger.info(
        "task placed",
        task_id=task.task_id,
        target=placement.target,
        score=placement.score,
        reason=placement.reason,
    )
    return TaskSubmitResponse(task_id=task.task_id, placement=placement)


@router.get("/{task_id}", response_model=TaskPlacement)
async def get_task_placement(
    task_id: str,
    request: Request,
) -> TaskPlacement:
    """Get the placement result for a previously submitted task."""
    engine = request.app.state.scheduler_engine
    result = engine.get_result(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return result


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    request: Request,
    limit: int = Query(50, ge=1, le=500, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> TaskListResponse:
    """List recent task placements with optional pagination."""
    engine = request.app.state.scheduler_engine
    all_results = list(engine.get_all_results().values())
    total = len(all_results)

    # Sort by task_id descending (newest first) and apply pagination
    all_results.sort(key=lambda p: p.task_id, reverse=True)
    paginated = all_results[offset : offset + limit]

    return TaskListResponse(total=total, tasks=paginated)
