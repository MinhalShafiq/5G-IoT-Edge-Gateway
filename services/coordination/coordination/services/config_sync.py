"""Distributed configuration synchronization.

The leader writes config changes to Redis (source of truth) and publishes
notifications on the ``config_updates`` Pub/Sub channel.  Follower nodes
subscribe to the channel and apply updates to their local state.  On
startup every node pulls the full config from Redis to converge quickly.
"""

from __future__ import annotations

import asyncio
import json

from shared.database.redis_client import RedisClient
from shared.observability.logging_config import get_logger

from coordination.state.cluster_state import ClusterState

logger = get_logger(__name__)

_CONFIG_UPDATES_CHANNEL = "config_updates"


class ConfigSync:
    """Manages distributed config replication across the edge cluster."""

    CONFIG_PREFIX = "iot-gateway:config:"

    def __init__(
        self,
        redis_client: RedisClient,
        cluster_state: ClusterState,
        node_id: str,
    ) -> None:
        self._redis = redis_client
        self._cluster_state = cluster_state
        self._node_id = node_id
        # Local cache: key -> (value, version)
        self._local_config: dict[str, tuple[str, int]] = {}

    # ------------------------------------------------------------------
    # Write path (leader only)
    # ------------------------------------------------------------------

    async def set_config(self, key: str, value: str) -> int:
        """Set a config value and broadcast the change.

        Only the leader should call this method.  Returns the new version.
        """
        version_key = f"{self.CONFIG_PREFIX}version:{key}"
        version: int = await self._redis.redis.incr(version_key)
        await self._redis.set(f"{self.CONFIG_PREFIX}{key}", value)

        self._local_config[key] = (value, version)
        self._cluster_state.set_config(key, value)

        notification = json.dumps(
            {
                "key": key,
                "value": value,
                "version": version,
                "updated_by": self._node_id,
            }
        )
        await self._redis.publish(_CONFIG_UPDATES_CHANNEL, notification)
        logger.info("config_set", key=key, version=version)
        return version

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    async def get_config(self, key: str) -> str | None:
        """Read a config value from Redis (source of truth)."""
        return await self._redis.get(f"{self.CONFIG_PREFIX}{key}")

    def get_local(self, key: str) -> tuple[str, int]:
        """Return the locally cached value and version for *key*.

        Returns ``("", 0)`` when the key is not in the local cache.
        """
        return self._local_config.get(key, ("", 0))

    def set_local(self, key: str, value: str, version: int) -> None:
        """Update the local cache directly (used by SyncConfig RPC)."""
        self._local_config[key] = (value, version)
        self._cluster_state.set_config(key, value)

    # ------------------------------------------------------------------
    # Full sync from Redis
    # ------------------------------------------------------------------

    async def sync_from_leader(self) -> int:
        """Pull all config keys from Redis (source of truth).

        Returns the number of keys synchronised.
        """
        cursor: int | str = 0
        count = 0
        prefix = self.CONFIG_PREFIX
        version_prefix = f"{prefix}version:"

        while True:
            cursor, keys = await self._redis.redis.scan(
                cursor=cursor,
                match=f"{prefix}*",
                count=100,
            )
            for raw_key in keys:
                key = raw_key if isinstance(raw_key, str) else raw_key.decode()
                # Skip version tracker keys
                if key.startswith(version_prefix):
                    continue
                short_key = key[len(prefix) :]
                value = await self._redis.get(key)
                if value is not None:
                    # Fetch the version
                    raw_ver = await self._redis.get(f"{version_prefix}{short_key}")
                    version = int(raw_ver) if raw_ver else 1
                    self._local_config[short_key] = (value, version)
                    self._cluster_state.set_config(short_key, value)
                    count += 1
            if cursor == 0:
                break

        logger.info("config_synced_from_redis", keys_synced=count)
        return count

    # ------------------------------------------------------------------
    # Background subscriber
    # ------------------------------------------------------------------

    async def run_sync_loop(self, shutdown_event: asyncio.Event) -> None:
        """Subscribe to ``config_updates`` Pub/Sub channel and apply changes.

        Runs until *shutdown_event* is set.
        """
        # Initial full sync
        try:
            await self.sync_from_leader()
        except Exception as exc:
            logger.error("initial_config_sync_failed", error=str(exc))

        pubsub = self._redis.redis.pubsub()
        await pubsub.subscribe(_CONFIG_UPDATES_CHANNEL)

        try:
            while not shutdown_event.is_set():
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message is not None and message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        key: str = data["key"]
                        value: str = data["value"]
                        version: int = data["version"]
                        updated_by: str = data.get("updated_by", "")

                        # Only apply if the incoming version is newer
                        _, local_version = self.get_local(key)
                        if version > local_version:
                            self.set_local(key, value, version)
                            logger.info(
                                "config_updated_via_pubsub",
                                key=key,
                                version=version,
                                updated_by=updated_by,
                            )
                    except (json.JSONDecodeError, KeyError) as exc:
                        logger.warning("config_sync_bad_message", error=str(exc))
        finally:
            await pubsub.unsubscribe(_CONFIG_UPDATES_CHANNEL)
            await pubsub.aclose()
