"""SWIM-like gossip protocol for membership management.

Each gossip round, the local node selects a random subset of alive peers
and probes them.  If a peer does not respond, it is marked ``SUSPECT``.
Over time, suspect nodes that never recover are promoted to ``DEAD`` by the
heartbeat service.

The current implementation uses Redis Pub/Sub as the signalling transport;
in production this would be replaced with direct gRPC or UDP probes.
"""

from __future__ import annotations

import asyncio
import json
import random
import time

from shared.database.redis_client import RedisClient
from shared.observability.logging_config import get_logger

from coordination.state.cluster_state import ClusterNode, ClusterState

logger = get_logger(__name__)

# Redis Pub/Sub channel used for gossip pings
_GOSSIP_CHANNEL = "iot-gateway:gossip"


class GossipProtocol:
    """SWIM-inspired gossip protocol for cluster membership probing."""

    def __init__(
        self,
        cluster_state: ClusterState,
        node_id: str,
        interval: float = 2.0,
        fanout: int = 3,
        redis_client: RedisClient | None = None,
    ) -> None:
        self._cluster_state = cluster_state
        self._node_id = node_id
        self._interval = interval
        self._fanout = fanout
        self._redis = redis_client
        self._sequence: int = 0

    def set_redis(self, redis_client: RedisClient) -> None:
        """Late-bind a Redis client (useful when the client is created after init)."""
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self, shutdown_event: asyncio.Event) -> None:
        """Main gossip loop: pick random peers, send ping, handle responses."""
        # Optionally start a listener for incoming pings
        listener_task: asyncio.Task[None] | None = None
        if self._redis is not None:
            listener_task = asyncio.create_task(
                self._listen_for_pings(shutdown_event),
                name="gossip_listener",
            )

        try:
            while not shutdown_event.is_set():
                await self._gossip_round()
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=self._interval,
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            if listener_task is not None:
                listener_task.cancel()
                try:
                    await listener_task
                except asyncio.CancelledError:
                    pass

    # ------------------------------------------------------------------
    # Per-round logic
    # ------------------------------------------------------------------

    async def _gossip_round(self) -> None:
        """Select a random subset of peers and probe them."""
        peers = self._cluster_state.get_alive_peers(exclude=self._node_id)
        if not peers:
            return

        targets = random.sample(peers, min(self._fanout, len(peers)))
        for peer in targets:
            success = await self._ping_peer(peer)
            if not success:
                self._cluster_state.mark_suspect(peer.node_id)
                logger.debug(
                    "gossip_peer_suspect",
                    target=peer.node_id,
                )

    async def _ping_peer(self, peer: ClusterNode) -> bool:
        """Send a lightweight probe to a peer via Redis Pub/Sub.

        Returns ``True`` if the publish succeeded (a proxy for reachability
        when using the Redis transport).  A real implementation would use
        direct gRPC or UDP and wait for an ACK.
        """
        if self._redis is None:
            return False

        self._sequence += 1
        payload = json.dumps(
            {
                "type": "ping",
                "from_node_id": self._node_id,
                "target_node_id": peer.node_id,
                "sequence": self._sequence,
                "timestamp": time.time(),
            }
        )
        try:
            await self._redis.publish(_GOSSIP_CHANNEL, payload)
            return True
        except Exception as exc:
            logger.warning("gossip_ping_failed", target=peer.node_id, error=str(exc))
            return False

    # ------------------------------------------------------------------
    # Incoming pings
    # ------------------------------------------------------------------

    async def handle_ping(self, from_node_id: str, from_address: str) -> None:
        """Handle an incoming ping -- update the sender's last-seen time."""
        self._cluster_state.update_node_heartbeat(from_node_id)
        logger.debug("gossip_ping_received", from_node_id=from_node_id)

    async def _listen_for_pings(self, shutdown_event: asyncio.Event) -> None:
        """Subscribe to the gossip channel and process incoming pings."""
        if self._redis is None:
            return

        pubsub = self._redis.redis.pubsub()
        await pubsub.subscribe(_GOSSIP_CHANNEL)

        try:
            while not shutdown_event.is_set():
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message is not None and message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        target = data.get("target_node_id", "")
                        if target == self._node_id:
                            await self.handle_ping(
                                from_node_id=data["from_node_id"],
                                from_address="",
                            )
                    except (json.JSONDecodeError, KeyError) as exc:
                        logger.warning("gossip_bad_message", error=str(exc))
        finally:
            await pubsub.unsubscribe(_GOSSIP_CHANNEL)
            await pubsub.aclose()
