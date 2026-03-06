"""In-memory cluster state management.

Maintains a local view of the cluster's membership and shared configuration.
Every node keeps its own ``ClusterState`` instance that is updated via
heartbeats, the gossip protocol, and config sync.  Thread-safety is achieved
through a simple ``threading.Lock`` (the async event-loop is single-threaded
so contention is minimal; the lock guards against potential executor
callbacks).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from shared.observability.logging_config import get_logger

logger = get_logger(__name__)


class NodeStatus(str, Enum):
    """Lifecycle states for a cluster node."""

    JOINING = "joining"
    ALIVE = "alive"
    SUSPECT = "suspect"
    DEAD = "dead"
    LEAVING = "leaving"


@dataclass
class ClusterNode:
    """Represents a single node in the edge cluster."""

    node_id: str
    node_address: str
    status: NodeStatus = NodeStatus.JOINING
    is_leader: bool = False
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


class ClusterState:
    """In-memory, thread-safe view of the edge cluster."""

    def __init__(self, local_node_id: str) -> None:
        self._local_node_id = local_node_id
        self._nodes: dict[str, ClusterNode] = {}
        self._cluster_version: int = 0
        self._config: dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Node management
    # ------------------------------------------------------------------

    def add_node(self, node: ClusterNode) -> None:
        """Add or replace a node in the cluster."""
        with self._lock:
            self._nodes[node.node_id] = node
            self._cluster_version += 1
            logger.debug("node_added", node_id=node.node_id, status=str(node.status))

    def remove_node(self, node_id: str) -> None:
        """Remove a node entirely from the cluster state."""
        with self._lock:
            if node_id in self._nodes:
                del self._nodes[node_id]
                self._cluster_version += 1
                logger.debug("node_removed", node_id=node_id)

    def get_node(self, node_id: str) -> ClusterNode | None:
        """Return a node by its ID, or ``None`` if not found."""
        with self._lock:
            return self._nodes.get(node_id)

    # ------------------------------------------------------------------
    # Heartbeat & status transitions
    # ------------------------------------------------------------------

    def update_node_heartbeat(self, node_id: str) -> None:
        """Refresh a node's heartbeat timestamp and set it ALIVE."""
        with self._lock:
            node = self._nodes.get(node_id)
            if node is not None:
                node.last_heartbeat = datetime.now(tz=timezone.utc)
                if node.status in (NodeStatus.JOINING, NodeStatus.SUSPECT):
                    node.status = NodeStatus.ALIVE

    def mark_suspect(self, node_id: str) -> None:
        """Transition a node to SUSPECT."""
        with self._lock:
            node = self._nodes.get(node_id)
            if node is not None and node.status not in (NodeStatus.DEAD, NodeStatus.LEAVING):
                node.status = NodeStatus.SUSPECT
                self._cluster_version += 1

    def mark_dead(self, node_id: str) -> None:
        """Transition a node to DEAD."""
        with self._lock:
            node = self._nodes.get(node_id)
            if node is not None:
                node.status = NodeStatus.DEAD
                node.is_leader = False
                self._cluster_version += 1

    def mark_alive(self, node_id: str) -> None:
        """Transition a node back to ALIVE."""
        with self._lock:
            node = self._nodes.get(node_id)
            if node is not None:
                node.status = NodeStatus.ALIVE
                node.last_heartbeat = datetime.now(tz=timezone.utc)
                self._cluster_version += 1

    def mark_leaving(self, node_id: str) -> None:
        """Transition a node to LEAVING."""
        with self._lock:
            node = self._nodes.get(node_id)
            if node is not None:
                node.status = NodeStatus.LEAVING
                self._cluster_version += 1

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_alive_peers(self, exclude: str | None = None) -> list[ClusterNode]:
        """Return all ALIVE nodes, optionally excluding one node_id."""
        with self._lock:
            return [
                n
                for n in self._nodes.values()
                if n.status == NodeStatus.ALIVE and n.node_id != exclude
            ]

    def get_all_nodes(self) -> list[ClusterNode]:
        """Return a snapshot of all known nodes."""
        with self._lock:
            return list(self._nodes.values())

    def get_leader(self) -> ClusterNode | None:
        """Return the node currently marked as leader, or ``None``."""
        with self._lock:
            for node in self._nodes.values():
                if node.is_leader:
                    return node
            return None

    def set_leader(self, node_id: str) -> None:
        """Mark *node_id* as the leader and clear the flag on all others."""
        with self._lock:
            for node in self._nodes.values():
                node.is_leader = node.node_id == node_id
            self._cluster_version += 1

    # ------------------------------------------------------------------
    # Shared config
    # ------------------------------------------------------------------

    def set_config(self, key: str, value: str) -> None:
        """Store a config key/value pair in the cluster-wide map."""
        with self._lock:
            self._config[key] = value

    def get_config(self, key: str) -> str | None:
        """Retrieve a config value by key."""
        with self._lock:
            return self._config.get(key)

    @property
    def config(self) -> dict[str, str]:
        """Return a copy of the shared config dictionary."""
        with self._lock:
            return dict(self._config)

    # ------------------------------------------------------------------
    # Version
    # ------------------------------------------------------------------

    @property
    def cluster_version(self) -> int:
        """Monotonically increasing version bumped on every state mutation."""
        return self._cluster_version
