"""Latency-first placement policy.

Always prefers edge placement to minimise network hops and round-trip time.
Falls back to cloud **only** when every known edge node is overloaded:

* CPU usage > 85 %, **or**
* Memory usage > 90 %

Among healthy edge nodes the policy prefers the requesting node (local
inference) and, failing that, the node with the lowest CPU utilisation.
"""

from __future__ import annotations

import structlog

from scheduler.services.placement_policy import PlacementPolicy
from scheduler.services.resource_monitor import NodeResources
from scheduler.services.scheduler_engine import InferenceTask, TaskPlacement

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

_CPU_OVERLOAD_PERCENT = 85.0
_MEMORY_OVERLOAD_PERCENT = 90.0


class LatencyFirstPolicy(PlacementPolicy):
    """Minimize latency by always preferring the nearest edge node."""

    def __init__(self, edge_latency_sla_ms: float = 100.0) -> None:
        self._edge_latency_sla_ms = edge_latency_sla_ms

    def evaluate(
        self,
        task: InferenceTask,
        resources: dict[str, NodeResources],
    ) -> TaskPlacement:
        # No edge nodes known -> cloud
        if not resources:
            return TaskPlacement(
                task_id=task.task_id,
                target="cloud",
                estimated_latency_ms=50.0,
                score=0.3,
                reason="latency_first: no edge nodes available",
            )

        # Partition nodes into healthy and overloaded
        healthy: list[NodeResources] = []
        for node in resources.values():
            if (
                node.cpu_usage_percent > _CPU_OVERLOAD_PERCENT
                or node.memory_usage_percent > _MEMORY_OVERLOAD_PERCENT
            ):
                continue
            healthy.append(node)

        # All edge nodes overloaded -> cloud fallback
        if not healthy:
            return TaskPlacement(
                task_id=task.task_id,
                target="cloud",
                estimated_latency_ms=50.0,
                score=0.35,
                reason="latency_first: all edge nodes overloaded, falling back to cloud",
            )

        # Prefer the requesting node if it is healthy
        best: NodeResources | None = None
        for node in healthy:
            if node.node_id == task.requesting_node_id:
                best = node
                break

        # Otherwise pick the least-loaded node
        if best is None:
            healthy.sort(key=lambda n: n.cpu_usage_percent)
            best = healthy[0]

        is_local = best.node_id == task.requesting_node_id
        target = "edge_local" if is_local else "edge_remote"

        # Score: 0.9 for local, 0.8 for remote — always high because this
        # policy strongly favours edge.
        estimated = best.avg_inference_latency_ms if best.avg_inference_latency_ms > 0 else 10.0
        score = 0.9 if is_local else 0.8

        return TaskPlacement(
            task_id=task.task_id,
            target=target,
            target_node_id=best.node_id,
            target_endpoint=best.node_address,
            estimated_latency_ms=estimated,
            score=score,
            reason=f"latency_first: {target} on {best.node_id} (cpu={best.cpu_usage_percent:.0f}%, mem={best.memory_usage_percent:.0f}%)",
        )
