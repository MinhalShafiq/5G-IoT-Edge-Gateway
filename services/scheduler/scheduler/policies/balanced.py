"""Balanced placement policy.

Combines latency and cost scores with configurable weights:

    combined = latency_weight * latency_score + cost_weight * cost_score

Defaults to 0.6 * latency + 0.4 * cost.  For each candidate (edge and cloud)
the policy computes individual latency and cost scores, combines them, and
returns the candidate with the highest weighted total.
"""

from __future__ import annotations

import structlog

from scheduler.services.placement_policy import PlacementPolicy
from scheduler.services.resource_monitor import NodeResources
from scheduler.services.scheduler_engine import InferenceTask, TaskPlacement

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_LATENCY_WEIGHT = 0.6
_COST_WEIGHT = 0.4
_SMALL_MODEL_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
_CPU_OVERLOAD_PERCENT = 85.0
_MEMORY_OVERLOAD_PERCENT = 90.0


class BalancedPolicy(PlacementPolicy):
    """Weighted combination of latency and cost scores."""

    def __init__(
        self,
        edge_latency_sla_ms: float = 100.0,
        latency_weight: float = _LATENCY_WEIGHT,
        cost_weight: float = _COST_WEIGHT,
    ) -> None:
        self._edge_latency_sla_ms = edge_latency_sla_ms
        self._latency_weight = latency_weight
        self._cost_weight = cost_weight

    # -- Score helpers --------------------------------------------------------

    def _latency_score_edge(self, node: NodeResources, task: InferenceTask) -> float:
        """Latency score for an edge placement (0.0 - 1.0).

        Higher when the node's average latency is well within the task's
        max_latency_ms constraint.
        """
        avg_lat = node.avg_inference_latency_ms if node.avg_inference_latency_ms > 0 else 10.0
        if avg_lat >= task.max_latency_ms:
            return 0.0
        # Linear scale: 1.0 at 0 ms, 0.0 at max_latency_ms
        return max(0.0, 1.0 - avg_lat / task.max_latency_ms)

    @staticmethod
    def _latency_score_cloud(task: InferenceTask) -> float:
        """Latency score for a cloud placement.

        Cloud latency is assumed ~50 ms (network + inference).
        """
        cloud_latency_ms = 50.0
        if cloud_latency_ms >= task.max_latency_ms:
            return 0.0
        return max(0.0, 1.0 - cloud_latency_ms / task.max_latency_ms)

    def _cost_score_edge(self, task: InferenceTask) -> float:
        """Cost score for edge placement.

        Edge is cheap for small models, less efficient for large ones.
        """
        if task.input_size_bytes < _SMALL_MODEL_SIZE_BYTES:
            return 0.9  # near-free
        # Large models on edge — still cheaper than cloud, but less efficient
        return 0.5

    @staticmethod
    def _cost_score_cloud(task: InferenceTask) -> float:
        """Cost score for cloud placement.

        Cloud has per-request cost, but offers better throughput for large models.
        """
        if task.input_size_bytes < _SMALL_MODEL_SIZE_BYTES:
            return 0.3  # wasteful for small models
        return 0.7  # efficient for large models

    # -- PlacementPolicy interface -------------------------------------------

    def evaluate(
        self,
        task: InferenceTask,
        resources: dict[str, NodeResources],
    ) -> TaskPlacement:
        # -- Score edge candidates --------------------------------------------
        best_edge_score: float = -1.0
        best_edge_node: NodeResources | None = None

        for node in resources.values():
            if (
                node.cpu_usage_percent > _CPU_OVERLOAD_PERCENT
                or node.memory_usage_percent > _MEMORY_OVERLOAD_PERCENT
            ):
                continue
            lat_score = self._latency_score_edge(node, task)
            cost_score = self._cost_score_edge(task)
            combined = self._latency_weight * lat_score + self._cost_weight * cost_score
            if combined > best_edge_score:
                best_edge_score = combined
                best_edge_node = node

        # -- Score cloud candidate --------------------------------------------
        cloud_lat_score = self._latency_score_cloud(task)
        cloud_cost_score = self._cost_score_cloud(task)
        cloud_score = self._latency_weight * cloud_lat_score + self._cost_weight * cloud_cost_score

        # -- Pick best --------------------------------------------------------
        if best_edge_node is not None and best_edge_score >= cloud_score:
            is_local = best_edge_node.node_id == task.requesting_node_id
            target = "edge_local" if is_local else "edge_remote"
            estimated = (
                best_edge_node.avg_inference_latency_ms
                if best_edge_node.avg_inference_latency_ms > 0
                else 10.0
            )
            return TaskPlacement(
                task_id=task.task_id,
                target=target,
                target_node_id=best_edge_node.node_id,
                target_endpoint=best_edge_node.node_address,
                estimated_latency_ms=estimated,
                score=round(best_edge_score, 4),
                reason=(
                    f"balanced: edge wins "
                    f"(edge={best_edge_score:.3f} vs cloud={cloud_score:.3f}, "
                    f"w_lat={self._latency_weight}, w_cost={self._cost_weight})"
                ),
            )

        return TaskPlacement(
            task_id=task.task_id,
            target="cloud",
            estimated_latency_ms=50.0,
            score=round(cloud_score, 4),
            reason=(
                f"balanced: cloud wins "
                f"(cloud={cloud_score:.3f}"
                f"{f' vs edge={best_edge_score:.3f}' if best_edge_node else ', no healthy edge'}, "
                f"w_lat={self._latency_weight}, w_cost={self._cost_weight})"
            ),
        )
