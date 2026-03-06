"""Core scheduling engine for ML inference task placement.

Evaluates all registered placement policies against live resource data and
selects the placement with the highest score.  Tasks can also be queued and
drained in a background loop for asynchronous scheduling.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from scheduler.services.placement_policy import PlacementPolicy
from scheduler.services.resource_monitor import ResourceMonitor

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class InferenceTask(BaseModel):
    """An ML inference task submitted for placement."""

    task_id: str = Field(default_factory=lambda: str(uuid4()))
    model_name: str
    model_version: str = "latest"
    input_size_bytes: int = 0
    priority: str = "medium"  # "low", "medium", "high"
    max_latency_ms: float = 100.0
    requesting_node_id: str = ""


class TaskPlacement(BaseModel):
    """Result of the placement decision for a single task."""

    task_id: str
    target: str  # "edge_local", "edge_remote", "cloud"
    target_node_id: str = ""
    target_endpoint: str = ""
    estimated_latency_ms: float = 0
    score: float = 0
    reason: str = ""


# ---------------------------------------------------------------------------
# Scheduling engine
# ---------------------------------------------------------------------------


class SchedulerEngine:
    """Evaluate placement policies and pick the best target for each task.

    The engine can process tasks synchronously via ``submit_task`` or queue
    them for background processing via ``enqueue_task`` / ``drain_queue``.
    """

    def __init__(
        self,
        resource_monitor: ResourceMonitor,
        policies: list[PlacementPolicy],
    ) -> None:
        self._resource_monitor = resource_monitor
        self._policies = policies
        self._task_queue: asyncio.Queue[InferenceTask] = asyncio.Queue()
        self._results: dict[str, TaskPlacement] = {}

    # -- Synchronous (request-path) scheduling --------------------------------

    async def submit_task(self, task: InferenceTask) -> TaskPlacement:
        """Evaluate all policies against current resources and return the
        placement with the highest score."""
        resources = self._resource_monitor.get_resource_map()

        best_placement: TaskPlacement | None = None
        best_score: float = -1.0

        for policy in self._policies:
            try:
                placement = policy.evaluate(task, resources)
                if placement.score > best_score:
                    best_score = placement.score
                    best_placement = placement
            except Exception:
                logger.exception(
                    "policy evaluation failed",
                    policy=type(policy).__name__,
                    task_id=task.task_id,
                )

        # Fallback: if no policy returned a result, default to cloud
        if best_placement is None:
            best_placement = TaskPlacement(
                task_id=task.task_id,
                target="cloud",
                reason="no policy returned a placement; defaulting to cloud",
            )

        self._results[task.task_id] = best_placement
        return best_placement

    # -- Asynchronous (queue-based) scheduling --------------------------------

    async def enqueue_task(self, task: InferenceTask) -> None:
        """Add a task to the internal queue for background processing."""
        await self._task_queue.put(task)

    async def drain_queue(self) -> int:
        """Process all tasks currently in the queue.  Returns the count of
        tasks processed during this drain cycle."""
        processed = 0
        while not self._task_queue.empty():
            try:
                task = self._task_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            await self.submit_task(task)
            processed += 1
        return processed

    # -- Result access --------------------------------------------------------

    def get_result(self, task_id: str) -> TaskPlacement | None:
        """Retrieve the placement result for a given task."""
        return self._results.get(task_id)

    def get_all_results(self) -> dict[str, TaskPlacement]:
        """Return a copy of all known placement results."""
        return dict(self._results)
