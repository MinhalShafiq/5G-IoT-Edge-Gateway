"""OTA (over-the-air) firmware rollout coordination.

Provides rolling and canary update strategies.  During a rolling update
nodes are updated one at a time; after each update the coordinator waits
for a health check before proceeding.  A canary update promotes a single
node first and, if it stays healthy for a configurable window, proceeds
to the remainder of the cluster.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from shared.database.redis_client import RedisClient
from shared.observability.logging_config import get_logger

from coordination.state.cluster_state import ClusterNode, ClusterState, NodeStatus

logger = get_logger(__name__)

# Redis key prefix for tracking OTA state
_OTA_PREFIX = "iot-gateway:ota:"


class OTACoordinator:
    """Coordinates firmware rollout across edge cluster nodes."""

    def __init__(
        self,
        cluster_state: ClusterState,
        redis_client: RedisClient,
    ) -> None:
        self._cluster_state = cluster_state
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Rolling update
    # ------------------------------------------------------------------

    async def start_rolling_update(
        self,
        firmware_version: str,
        nodes: list[ClusterNode] | None = None,
        health_timeout: float = 60.0,
    ) -> dict[str, str]:
        """Rolling update: update one node at a time, verify health, continue.

        Returns a mapping of ``node_id -> status`` for every targeted node.
        Raises on first health-check failure after attempting a rollback.
        """
        target_nodes = nodes or self._cluster_state.get_alive_peers()
        results: dict[str, str] = {}

        logger.info(
            "rolling_update_started",
            firmware_version=firmware_version,
            target_count=len(target_nodes),
        )

        await self._record_ota_event(
            firmware_version,
            "rolling_update_started",
            node_ids=[n.node_id for n in target_nodes],
        )

        for node in target_nodes:
            try:
                await self._update_node(node, firmware_version)
                healthy = await self._wait_for_health(node, timeout=health_timeout)
                if not healthy:
                    await self._rollback_node(node)
                    results[node.node_id] = "rollback"
                    await self._record_ota_event(
                        firmware_version,
                        "rolling_update_failed",
                        node_ids=[node.node_id],
                    )
                    raise RuntimeError(
                        f"Node {node.node_id} failed health check after update "
                        f"to firmware {firmware_version}"
                    )
                results[node.node_id] = "updated"
                logger.info(
                    "node_update_success",
                    node_id=node.node_id,
                    firmware_version=firmware_version,
                )
            except RuntimeError:
                raise
            except Exception as exc:
                results[node.node_id] = f"error: {exc}"
                logger.error(
                    "node_update_error",
                    node_id=node.node_id,
                    error=str(exc),
                )
                raise

        await self._record_ota_event(firmware_version, "rolling_update_completed")
        logger.info("rolling_update_completed", firmware_version=firmware_version)
        return results

    # ------------------------------------------------------------------
    # Canary update
    # ------------------------------------------------------------------

    async def start_canary_update(
        self,
        firmware_version: str,
        canary_watch_seconds: float = 120.0,
        health_timeout: float = 60.0,
    ) -> dict[str, str]:
        """Canary: update 1 node, monitor for *canary_watch_seconds*, then update the rest.

        Returns a mapping of ``node_id -> status``.
        """
        alive_nodes = self._cluster_state.get_alive_peers()
        if not alive_nodes:
            logger.warning("canary_update_no_nodes")
            return {}

        canary = alive_nodes[0]
        remaining = alive_nodes[1:]
        results: dict[str, str] = {}

        logger.info(
            "canary_update_started",
            firmware_version=firmware_version,
            canary_node=canary.node_id,
            remaining_count=len(remaining),
        )

        await self._record_ota_event(
            firmware_version,
            "canary_update_started",
            node_ids=[canary.node_id],
        )

        # Phase 1: update canary
        await self._update_node(canary, firmware_version)
        healthy = await self._wait_for_health(canary, timeout=health_timeout)
        if not healthy:
            await self._rollback_node(canary)
            results[canary.node_id] = "rollback"
            raise RuntimeError(
                f"Canary node {canary.node_id} failed health check "
                f"for firmware {firmware_version}"
            )

        results[canary.node_id] = "canary_updated"

        # Phase 2: observe canary
        logger.info(
            "canary_watch_period",
            canary_node=canary.node_id,
            watch_seconds=canary_watch_seconds,
        )
        await asyncio.sleep(canary_watch_seconds)

        # Re-check canary health after the observation window
        healthy = await self._wait_for_health(canary, timeout=health_timeout)
        if not healthy:
            await self._rollback_node(canary)
            results[canary.node_id] = "rollback"
            raise RuntimeError(
                f"Canary node {canary.node_id} degraded during watch period"
            )

        # Phase 3: roll out to remaining nodes
        for node in remaining:
            await self._update_node(node, firmware_version)
            node_healthy = await self._wait_for_health(node, timeout=health_timeout)
            if not node_healthy:
                await self._rollback_node(node)
                results[node.node_id] = "rollback"
                raise RuntimeError(
                    f"Node {node.node_id} failed health check after update"
                )
            results[node.node_id] = "updated"

        await self._record_ota_event(firmware_version, "canary_update_completed")
        logger.info("canary_update_completed", firmware_version=firmware_version)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _update_node(self, node: ClusterNode, firmware_version: str) -> None:
        """Instruct a node to apply a firmware update.

        In a real deployment this would send a gRPC call to the target node's
        management endpoint or publish an update command to a topic that the
        node's agent is subscribed to.
        """
        logger.info(
            "node_update_initiated",
            node_id=node.node_id,
            firmware_version=firmware_version,
        )
        node.metadata["pending_firmware"] = firmware_version
        await self._redis.set(
            f"{_OTA_PREFIX}node:{node.node_id}:target_version",
            firmware_version,
        )

    async def _wait_for_health(
        self,
        node: ClusterNode,
        timeout: float = 60.0,
        poll_interval: float = 2.0,
    ) -> bool:
        """Wait up to *timeout* seconds for *node* to report healthy.

        A node is considered healthy when its status in the cluster state
        is ALIVE and it has sent a heartbeat recently.
        """
        elapsed = 0.0
        while elapsed < timeout:
            cluster_node = self._cluster_state.get_node(node.node_id)
            if cluster_node is not None and cluster_node.status == NodeStatus.ALIVE:
                return True
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        return False

    async def _rollback_node(self, node: ClusterNode) -> None:
        """Instruct a node to roll back to its previous firmware version.

        Similar to ``_update_node`` this is a placeholder; a production
        system would invoke the node's management API.
        """
        logger.warning("node_rollback_initiated", node_id=node.node_id)
        node.metadata.pop("pending_firmware", None)
        await self._redis.delete(f"{_OTA_PREFIX}node:{node.node_id}:target_version")

    async def _record_ota_event(
        self,
        firmware_version: str,
        event: str,
        node_ids: list[str] | None = None,
    ) -> None:
        """Persist an OTA event to Redis for audit / observability."""
        payload = json.dumps(
            {
                "firmware_version": firmware_version,
                "event": event,
                "node_ids": node_ids or [],
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }
        )
        await self._redis.redis.rpush(f"{_OTA_PREFIX}events", payload)
