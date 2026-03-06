"""gRPC service implementation for the Coordination service.

This is a *logical* servicer -- each method matches the intended RPC from
the ``coordination.proto`` definition.  Until proto stubs are generated the
methods act as plain async helpers that can be invoked directly by tests or
by the gRPC reflection layer.

Expected proto RPCs:
    - Heartbeat         (HeartbeatRequest)     -> HeartbeatResponse
    - JoinCluster       (JoinRequest)          -> JoinResponse
    - LeaveCluster      (LeaveRequest)         -> LeaveResponse
    - GetClusterState   (Empty)                -> ClusterStateResponse
    - SyncConfig        (stream ConfigEntry)   -> stream ConfigEntry  (bidi)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator

from shared.observability.logging_config import get_logger

from coordination.services.config_sync import ConfigSync
from coordination.services.leader_election import LeaderElection
from coordination.state.cluster_state import ClusterNode, ClusterState, NodeStatus

logger = get_logger(__name__)


class CoordinationServicer:
    """Implements the Coordination gRPC service methods."""

    def __init__(
        self,
        cluster_state: ClusterState,
        leader_election: LeaderElection,
        config_sync: ConfigSync,
    ) -> None:
        self.cluster_state = cluster_state
        self.leader_election = leader_election
        self.config_sync = config_sync

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def Heartbeat(self, request: Any, context: Any = None) -> dict[str, Any]:
        """Update a node's last-seen timestamp and return cluster metadata.

        Parameters
        ----------
        request : object
            Expected attributes: ``node_id``, ``node_address``.

        Returns
        -------
        dict with ``leader_id``, ``cluster_version``, ``ack``.
        """
        node_id: str = getattr(request, "node_id", "") or request.get("node_id", "")  # type: ignore[union-attr]
        node_address: str = getattr(request, "node_address", "") or request.get("node_address", "")  # type: ignore[union-attr]

        # Upsert the node
        existing = self.cluster_state.get_node(node_id)
        if existing is None:
            node = ClusterNode(
                node_id=node_id,
                node_address=node_address,
                status=NodeStatus.ALIVE,
            )
            self.cluster_state.add_node(node)
            logger.info("node_registered_via_heartbeat", node_id=node_id)
        else:
            self.cluster_state.update_node_heartbeat(node_id)
            if existing.status in (NodeStatus.SUSPECT, NodeStatus.JOINING):
                self.cluster_state.mark_alive(node_id)

        leader_id = await self.leader_election.get_leader() or ""

        return {
            "leader_id": leader_id,
            "cluster_version": self.cluster_state.cluster_version,
            "ack": True,
        }

    # ------------------------------------------------------------------
    # JoinCluster
    # ------------------------------------------------------------------

    async def JoinCluster(self, request: Any, context: Any = None) -> dict[str, Any]:
        """Register a new node in the cluster.

        Parameters
        ----------
        request : object
            Expected attributes: ``node_id``, ``node_address``, ``capabilities``.

        Returns
        -------
        dict with ``members`` (list of dicts) and ``config`` (dict).
        """
        node_id: str = getattr(request, "node_id", "") or request.get("node_id", "")  # type: ignore[union-attr]
        node_address: str = getattr(request, "node_address", "") or request.get("node_address", "")  # type: ignore[union-attr]
        capabilities: list[str] = getattr(request, "capabilities", []) or request.get("capabilities", [])  # type: ignore[union-attr]

        node = ClusterNode(
            node_id=node_id,
            node_address=node_address,
            status=NodeStatus.ALIVE,
            capabilities=list(capabilities),
        )
        self.cluster_state.add_node(node)
        logger.info("node_joined_cluster", node_id=node_id, address=node_address)

        # Return the current member list and shared config
        members = [
            {
                "node_id": n.node_id,
                "node_address": n.node_address,
                "status": str(n.status),
                "is_leader": n.is_leader,
            }
            for n in self.cluster_state.get_all_nodes()
        ]

        config = dict(self.cluster_state.config)

        return {
            "members": members,
            "config": config,
            "cluster_version": self.cluster_state.cluster_version,
        }

    # ------------------------------------------------------------------
    # LeaveCluster
    # ------------------------------------------------------------------

    async def LeaveCluster(self, request: Any, context: Any = None) -> dict[str, Any]:
        """Mark a node as LEAVING so it is excluded from future gossip rounds.

        Parameters
        ----------
        request : object
            Expected attribute: ``node_id``.
        """
        node_id: str = getattr(request, "node_id", "") or request.get("node_id", "")  # type: ignore[union-attr]

        existing = self.cluster_state.get_node(node_id)
        if existing is not None:
            self.cluster_state.mark_leaving(node_id)
            logger.info("node_leaving_cluster", node_id=node_id)
            return {"ack": True}

        logger.warning("leave_unknown_node", node_id=node_id)
        return {"ack": False, "error": "unknown node"}

    # ------------------------------------------------------------------
    # GetClusterState
    # ------------------------------------------------------------------

    async def GetClusterState(self, request: Any = None, context: Any = None) -> dict[str, Any]:
        """Return the full cluster state snapshot."""
        nodes = [
            {
                "node_id": n.node_id,
                "node_address": n.node_address,
                "status": str(n.status),
                "is_leader": n.is_leader,
                "last_heartbeat": n.last_heartbeat.isoformat(),
                "capabilities": n.capabilities,
                "metadata": n.metadata,
            }
            for n in self.cluster_state.get_all_nodes()
        ]

        leader = self.cluster_state.get_leader()
        leader_id = leader.node_id if leader else ""

        return {
            "nodes": nodes,
            "leader_id": leader_id,
            "cluster_version": self.cluster_state.cluster_version,
            "config": dict(self.cluster_state.config),
        }

    # ------------------------------------------------------------------
    # SyncConfig  (bidirectional streaming)
    # ------------------------------------------------------------------

    async def SyncConfig(
        self,
        request_iterator: AsyncIterator[Any],
        context: Any = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Bidirectional streaming for config propagation.

        The caller sends config entries it knows about; this servicer
        responds with any entries the caller is missing or that have a
        higher version.
        """
        async for entry in request_iterator:
            key: str = getattr(entry, "key", "") or entry.get("key", "")  # type: ignore[union-attr]
            value: str = getattr(entry, "value", "") or entry.get("value", "")  # type: ignore[union-attr]
            version: int = getattr(entry, "version", 0) or entry.get("version", 0)  # type: ignore[union-attr]

            local_value, local_version = self.config_sync.get_local(key)

            if local_version > version:
                # We have a newer version -- send it back
                yield {
                    "key": key,
                    "value": local_value,
                    "version": local_version,
                }
            elif version > local_version:
                # Caller has a newer version -- ingest it
                self.config_sync.set_local(key, value, version)
                logger.info(
                    "config_updated_via_sync",
                    key=key,
                    version=version,
                    from_node="remote",
                )
