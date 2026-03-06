"""Cost-aware placement policy.

Balances inference cost against placement.  Edge inference is essentially free
once hardware is provisioned, whereas cloud inference carries per-request API
costs.  The policy therefore:

* Prefers **edge** for small models (input < 10 MB) — cheap to run locally.
* Prefers **cloud** for large models where specialised cloud hardware
  (e.g. large GPUs) offers better throughput-per-dollar.
* Applies a configurable ``cloud_cost_factor`` that penalises cloud
  placements in the score.

Among eligible edge nodes the policy picks the one with the lowest CPU
utilisation.
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

_SMALL_MODEL_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
_CLOUD_COST_FACTOR = 0.15  # score penalty for cloud placement
_CPU_OVERLOAD_PERCENT = 85.0
_MEMORY_OVERLOAD_PERCENT = 90.0


class CostAwarePolicy(PlacementPolicy):
    """Prefer cheap edge inference for small models, cloud for large ones."""

    def __init__(
        self,
        cloud_endpoint: str = "http://cloud-api:8080",
        cloud_cost_factor: float = _CLOUD_COST_FACTOR,
        small_model_threshold_bytes: int = _SMALL_MODEL_SIZE_BYTES,
    ) -> None:
        self._cloud_endpoint = cloud_endpoint
        self._cloud_cost_factor = cloud_cost_factor
        self._small_model_threshold = small_model_threshold_bytes

    def evaluate(
        self,
        task: InferenceTask,
        resources: dict[str, NodeResources],
    ) -> TaskPlacement:
        is_small_model = task.input_size_bytes < self._small_model_threshold

        # Collect healthy edge nodes
        healthy_nodes: list[NodeResources] = [
            n
            for n in resources.values()
            if n.cpu_usage_percent <= _CPU_OVERLOAD_PERCENT
            and n.memory_usage_percent <= _MEMORY_OVERLOAD_PERCENT
        ]

        # --- Small model: prefer edge ---
        if is_small_model and healthy_nodes:
            # Pick the requesting node if healthy, else least-loaded
            best: NodeResources | None = None
            for node in healthy_nodes:
                if node.node_id == task.requesting_node_id:
                    best = node
                    break
            if best is None:
                healthy_nodes.sort(key=lambda n: n.cpu_usage_percent)
                best = healthy_nodes[0]

            is_local = best.node_id == task.requesting_node_id
            target = "edge_local" if is_local else "edge_remote"
            estimated = best.avg_inference_latency_ms if best.avg_inference_latency_ms > 0 else 15.0
            # Edge is free -> high score
            score = 0.85 if is_local else 0.75

            return TaskPlacement(
                task_id=task.task_id,
                target=target,
                target_node_id=best.node_id,
                target_endpoint=best.node_address,
                estimated_latency_ms=estimated,
                score=score,
                reason=f"cost_aware: small model ({task.input_size_bytes} B) -> edge {target}",
            )

        # --- Large model or no healthy edge nodes: prefer cloud ---
        cloud_score = 0.7 - self._cloud_cost_factor  # penalise for cloud cost

        # If there are no edge nodes at all, boost cloud score slightly
        if not resources:
            cloud_score = 0.6

        return TaskPlacement(
            task_id=task.task_id,
            target="cloud",
            target_endpoint=self._cloud_endpoint,
            estimated_latency_ms=40.0,
            score=max(0.1, cloud_score),
            reason=f"cost_aware: {'large model' if not is_small_model else 'no healthy edge'} ({task.input_size_bytes} B) -> cloud",
        )
