"""Adaptive edge-cloud scheduler with a feedback loop.

Maintains an exponential moving average of edge inference latency and
dynamically adjusts placement thresholds.  When edge latency stays below
the SLA target, tasks are preferentially placed on edge.  When edge nodes
become overloaded the scheduler shifts load to the cloud, and gradually
returns traffic to edge as conditions improve.
"""

from __future__ import annotations

from collections import deque

import structlog

from scheduler.services.resource_monitor import NodeResources, ResourceMonitor
from scheduler.services.scheduler_engine import InferenceTask, TaskPlacement
from scheduler.services.placement_policy import PlacementPolicy

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_WINDOW_SIZE = 50  # number of observations in the moving average
_CPU_OVERLOAD_THRESHOLD = 80.0  # percent
_MEMORY_OVERLOAD_THRESHOLD = 90.0  # percent
_EMA_ALPHA = 0.3  # smoothing factor for the exponential moving average


class AdaptiveScheduler(PlacementPolicy):
    """Placement policy that adapts based on a latency feedback loop.

    * If the moving-average edge latency is **below** the SLA, prefer edge.
    * If any edge node is overloaded (CPU > 80 % or memory > 90 %), shift
      new tasks to the cloud until conditions improve.
    * The CPU overload threshold is adjusted dynamically: lowered when
      latency is trending up, and raised when latency is well within SLA.
    """

    def __init__(
        self,
        resource_monitor: ResourceMonitor,
        edge_latency_sla_ms: float = 100.0,
        cloud_endpoint: str = "http://cloud-api:8080",
        window_size: int = _DEFAULT_WINDOW_SIZE,
    ) -> None:
        self._resource_monitor = resource_monitor
        self._edge_latency_sla_ms = edge_latency_sla_ms
        self._cloud_endpoint = cloud_endpoint

        # Feedback state
        self._latency_observations: deque[float] = deque(maxlen=window_size)
        self._ema_latency: float = 0.0
        self._dynamic_cpu_threshold: float = _CPU_OVERLOAD_THRESHOLD

    # -- Feedback interface ---------------------------------------------------

    def record_latency(self, latency_ms: float) -> None:
        """Feed an observed edge inference latency back into the scheduler.

        Call this after each edge inference completes so the adaptive loop
        can adjust its thresholds.
        """
        self._latency_observations.append(latency_ms)
        # Exponential moving average
        if self._ema_latency == 0.0:
            self._ema_latency = latency_ms
        else:
            self._ema_latency = (
                _EMA_ALPHA * latency_ms + (1 - _EMA_ALPHA) * self._ema_latency
            )
        self._adjust_thresholds()

    def _adjust_thresholds(self) -> None:
        """Dynamically adjust CPU overload threshold based on latency trend."""
        if self._ema_latency <= 0:
            return

        ratio = self._ema_latency / self._edge_latency_sla_ms
        if ratio < 0.5:
            # Latency well within SLA — relax the CPU threshold
            self._dynamic_cpu_threshold = min(90.0, self._dynamic_cpu_threshold + 1.0)
        elif ratio > 0.9:
            # Latency approaching SLA limit — tighten the CPU threshold
            self._dynamic_cpu_threshold = max(60.0, self._dynamic_cpu_threshold - 2.0)

        logger.debug(
            "adaptive threshold adjusted",
            ema_latency=round(self._ema_latency, 2),
            sla_ratio=round(ratio, 3),
            cpu_threshold=round(self._dynamic_cpu_threshold, 1),
        )

    # -- PlacementPolicy interface -------------------------------------------

    def evaluate(
        self,
        task: InferenceTask,
        resources: dict[str, NodeResources],
    ) -> TaskPlacement:
        """Adaptive placement: prefer edge if latency is within SLA and nodes
        are not overloaded; otherwise send to cloud."""
        # If no edge nodes are reporting, fall back to cloud
        if not resources:
            return TaskPlacement(
                task_id=task.task_id,
                target="cloud",
                target_endpoint=self._cloud_endpoint,
                estimated_latency_ms=50.0,
                score=0.5,
                reason="no edge nodes available; defaulting to cloud",
            )

        # Check if the EMA latency is within SLA
        latency_ok = (
            self._ema_latency <= self._edge_latency_sla_ms
            or self._ema_latency == 0.0  # no observations yet — optimistic
        )

        # Find a suitable edge node
        best_node: NodeResources | None = None
        for node in resources.values():
            is_overloaded = (
                node.cpu_usage_percent > self._dynamic_cpu_threshold
                or node.memory_usage_percent > _MEMORY_OVERLOAD_THRESHOLD
            )
            if is_overloaded:
                continue
            # Prefer the requesting node if it has capacity
            if node.node_id == task.requesting_node_id:
                best_node = node
                break
            if best_node is None or node.cpu_usage_percent < best_node.cpu_usage_percent:
                best_node = node

        if best_node is not None and latency_ok:
            # Place on edge
            is_local = best_node.node_id == task.requesting_node_id
            target = "edge_local" if is_local else "edge_remote"
            # Score: higher when latency is well within SLA
            sla_ratio = (
                self._ema_latency / self._edge_latency_sla_ms
                if self._ema_latency > 0
                else 0.3
            )
            score = max(0.0, min(1.0, 1.0 - sla_ratio * 0.5))
            return TaskPlacement(
                task_id=task.task_id,
                target=target,
                target_node_id=best_node.node_id,
                target_endpoint=best_node.node_address,
                estimated_latency_ms=best_node.avg_inference_latency_ms or self._ema_latency,
                score=score,
                reason=f"adaptive: edge {target}, ema_latency={self._ema_latency:.1f}ms, cpu_thresh={self._dynamic_cpu_threshold:.0f}%",
            )

        # Shift to cloud
        return TaskPlacement(
            task_id=task.task_id,
            target="cloud",
            target_endpoint=self._cloud_endpoint,
            estimated_latency_ms=50.0,
            score=0.4,
            reason=f"adaptive: edge overloaded or latency above SLA (ema={self._ema_latency:.1f}ms, threshold={self._dynamic_cpu_threshold:.0f}%)",
        )

    # -- Introspection -------------------------------------------------------

    @property
    def ema_latency(self) -> float:
        """Current exponential moving average of edge latency."""
        return self._ema_latency

    @property
    def dynamic_cpu_threshold(self) -> float:
        """Current dynamically adjusted CPU overload threshold."""
        return self._dynamic_cpu_threshold
