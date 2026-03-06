"""Resource monitor — tracks utilization of all edge nodes.

Edge nodes push periodic resource reports via the ``/resources/report``
endpoint.  The monitor stores the latest report per node and filters out
stale entries when the scheduler queries for available capacity.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


class NodeResources(BaseModel):
    """Snapshot of a single edge node's resource utilization."""

    node_id: str
    node_address: str = ""
    cpu_usage_percent: float = 0.0
    memory_usage_percent: float = 0.0
    gpu_usage_percent: float = 0.0
    pending_tasks: int = 0
    avg_inference_latency_ms: float = 0.0
    loaded_models: list[str] = Field(default_factory=list)
    last_reported: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class ResourceMonitor:
    """In-memory store of the latest resource report for each edge node.

    Reports older than ``stale_threshold`` seconds are excluded from the
    resource map returned to the scheduling engine.
    """

    def __init__(self, stale_threshold: float = 30.0) -> None:
        self._nodes: dict[str, NodeResources] = {}
        self._stale_threshold = stale_threshold

    # -- Mutations ------------------------------------------------------------

    def update(self, report: NodeResources) -> None:
        """Accept a new resource report from an edge node.

        Overwrites any previous report for the same ``node_id``.
        """
        # Ensure the report timestamp is current
        report.last_reported = datetime.now(timezone.utc)
        self._nodes[report.node_id] = report
        logger.debug(
            "resource report updated",
            node_id=report.node_id,
            cpu=report.cpu_usage_percent,
            memory=report.memory_usage_percent,
        )

    # -- Queries --------------------------------------------------------------

    def get_resource_map(self) -> dict[str, NodeResources]:
        """Return a mapping of ``node_id -> NodeResources`` excluding stale nodes."""
        now = datetime.now(timezone.utc)
        active: dict[str, NodeResources] = {}
        for node_id, node in self._nodes.items():
            # Ensure both datetimes are timezone-aware for comparison
            reported_at = node.last_reported
            if reported_at.tzinfo is None:
                reported_at = reported_at.replace(tzinfo=timezone.utc)
            age_seconds = (now - reported_at).total_seconds()
            if age_seconds <= self._stale_threshold:
                active[node_id] = node
            else:
                logger.debug(
                    "excluding stale node",
                    node_id=node_id,
                    age_seconds=round(age_seconds, 1),
                )
        return active

    def get_healthy_nodes(self) -> list[NodeResources]:
        """Return a list of non-stale nodes sorted by CPU usage (lowest first)."""
        active = self.get_resource_map()
        nodes = list(active.values())
        nodes.sort(key=lambda n: n.cpu_usage_percent)
        return nodes

    def get_node(self, node_id: str) -> NodeResources | None:
        """Get the raw report for a node regardless of staleness."""
        return self._nodes.get(node_id)

    @property
    def node_count(self) -> int:
        """Total number of nodes that have ever reported (including stale)."""
        return len(self._nodes)
