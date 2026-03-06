"""Node heartbeat tracking and timeout detection.

Periodically scans all known nodes and promotes stale entries from ALIVE
to SUSPECT and finally to DEAD based on how long they have been silent.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from shared.observability.logging_config import get_logger

from coordination.state.cluster_state import ClusterState, NodeStatus

logger = get_logger(__name__)


class HeartbeatService:
    """Monitors node liveness by checking heartbeat timestamps."""

    def __init__(
        self,
        cluster_state: ClusterState,
        node_id: str,
        interval: float = 2.0,
        timeout: float = 10.0,
    ) -> None:
        self._cluster_state = cluster_state
        self._node_id = node_id
        self._interval = interval
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def run(self, shutdown_event: asyncio.Event) -> None:
        """Periodically check all known nodes for heartbeat timeout.

        Runs until *shutdown_event* is set.
        """
        while not shutdown_event.is_set():
            self._check_timeouts()
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=self._interval,
                )
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------
    # Timeout detection
    # ------------------------------------------------------------------

    def _check_timeouts(self) -> None:
        """Mark nodes as SUSPECT or DEAD if their heartbeat is stale.

        Thresholds:
        - ``> timeout``      -> SUSPECT
        - ``> timeout * 2``  -> DEAD
        """
        now = datetime.now(tz=timezone.utc)

        for node in self._cluster_state.get_all_nodes():
            # Never timeout ourselves
            if node.node_id == self._node_id:
                continue
            # Don't re-evaluate nodes already DEAD or LEAVING
            if node.status in (NodeStatus.DEAD, NodeStatus.LEAVING):
                continue

            # Ensure we compare timezone-aware datetimes
            last_hb = node.last_heartbeat
            if last_hb.tzinfo is None:
                last_hb = last_hb.replace(tzinfo=timezone.utc)

            elapsed = (now - last_hb).total_seconds()

            if elapsed > self._timeout * 2:
                if node.status != NodeStatus.DEAD:
                    self._cluster_state.mark_dead(node.node_id)
                    logger.warning(
                        "node_declared_dead",
                        node_id=node.node_id,
                        elapsed_seconds=round(elapsed, 1),
                    )
            elif elapsed > self._timeout:
                if node.status != NodeStatus.SUSPECT:
                    self._cluster_state.mark_suspect(node.node_id)
                    logger.warning(
                        "node_suspected",
                        node_id=node.node_id,
                        elapsed_seconds=round(elapsed, 1),
                    )
