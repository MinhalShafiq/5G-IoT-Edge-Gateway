"""Redis-based distributed leader election.

Uses ``SET NX EX`` (atomic set-if-not-exists with TTL) so that exactly one
node in the cluster holds the leader lock at any time.  The lock is
periodically renewed; if a leader crashes, the TTL ensures the lock
expires and another node can take over.
"""

from __future__ import annotations

import asyncio

from shared.database.redis_client import RedisClient
from shared.observability.logging_config import get_logger

logger = get_logger(__name__)


class LeaderElection:
    """Redis-based leader election using a single key with TTL."""

    LOCK_KEY = "iot-gateway:leader"

    def __init__(
        self,
        redis_client: RedisClient,
        node_id: str,
        ttl_seconds: int = 15,
    ) -> None:
        self._redis = redis_client
        self._node_id = node_id
        self._ttl = ttl_seconds
        self._is_leader = False

    # ------------------------------------------------------------------
    # Core election logic
    # ------------------------------------------------------------------

    async def try_acquire(self) -> bool:
        """Try to become leader using ``SET NX EX`` (atomic set-if-not-exists with TTL).

        If we already hold the lock the TTL is renewed.

        Returns ``True`` if this node is (now) the leader.
        """
        result = await self._redis.set(
            self.LOCK_KEY, self._node_id, ex=self._ttl, nx=True
        )
        if result:
            if not self._is_leader:
                logger.info("leader_elected", node_id=self._node_id)
            self._is_leader = True
            return True

        # Check whether we already hold the lock
        current = await self._redis.get(self.LOCK_KEY)
        if current == self._node_id:
            # Renew TTL
            await self._redis.set(self.LOCK_KEY, self._node_id, ex=self._ttl)
            self._is_leader = True
            return True

        # Someone else is leader
        if self._is_leader:
            logger.warning("leader_lost", node_id=self._node_id, current_leader=current)
        self._is_leader = False
        return False

    async def get_leader(self) -> str | None:
        """Return the ``node_id`` of the current leader, or ``None``."""
        value = await self._redis.get(self.LOCK_KEY)
        return value

    async def release(self) -> None:
        """Release the leader lock if this node holds it."""
        if self._is_leader:
            current = await self._redis.get(self.LOCK_KEY)
            if current == self._node_id:
                await self._redis.delete(self.LOCK_KEY)
                logger.info("leader_released", node_id=self._node_id)
            self._is_leader = False

    @property
    def is_leader(self) -> bool:
        """Whether this node currently believes it is the leader."""
        return self._is_leader

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def run_election_loop(
        self,
        interval: float,
        shutdown_event: asyncio.Event,
    ) -> None:
        """Periodically try to acquire / renew the leader lock.

        Runs until *shutdown_event* is set.
        """
        while not shutdown_event.is_set():
            try:
                await self.try_acquire()
            except Exception as exc:
                logger.error("leader_election_error", error=str(exc))
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=interval,
                )
            except asyncio.TimeoutError:
                pass
