"""Redis async client with Streams helper methods.

Provides a unified client for Redis operations including Streams (XADD, XREADGROUP),
pub/sub, and standard key-value operations used across all services.
"""

import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class RedisClient:
    """Async Redis client wrapper with Streams support."""

    def __init__(self, url: str, max_connections: int = 20):
        self._url = url
        self._pool = aioredis.ConnectionPool.from_url(
            url, max_connections=max_connections, decode_responses=True
        )
        self._redis = aioredis.Redis(connection_pool=self._pool)

    @property
    def redis(self) -> aioredis.Redis:
        """Raw redis client for advanced operations."""
        return self._redis

    # --- Streams: Producer ---

    async def stream_add(
        self,
        stream: str,
        data: dict[str, str],
        maxlen: int | None = 100_000,
        approximate: bool = True,
    ) -> str:
        """Add an entry to a Redis Stream (XADD).

        Returns the auto-generated stream entry ID.
        """
        return await self._redis.xadd(
            stream, data, maxlen=maxlen, approximate=approximate
        )

    # --- Streams: Consumer Group ---

    async def ensure_consumer_group(
        self, stream: str, group: str, start_id: str = "0"
    ) -> None:
        """Create a consumer group if it doesn't exist. Idempotent."""
        try:
            await self._redis.xgroup_create(
                stream, group, id=start_id, mkstream=True
            )
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def stream_read_group(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: int = 2000,
    ) -> list[tuple[str, dict[str, str]]]:
        """Read new messages from a consumer group (XREADGROUP).

        Returns list of (entry_id, data) tuples.
        """
        result = await self._redis.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={stream: ">"},
            count=count,
            block=block_ms,
        )
        if not result:
            return []
        # result format: [[stream_name, [(id, data), ...]]]
        entries = result[0][1]
        return entries

    async def stream_ack(self, stream: str, group: str, *entry_ids: str) -> int:
        """Acknowledge processed messages (XACK)."""
        return await self._redis.xack(stream, group, *entry_ids)

    async def stream_pending(
        self, stream: str, group: str, count: int = 10
    ) -> list[dict[str, Any]]:
        """Get pending (unacknowledged) messages for a consumer group."""
        return await self._redis.xpending_range(
            stream, group, min="-", max="+", count=count
        )

    async def stream_len(self, stream: str) -> int:
        """Get the length of a stream."""
        return await self._redis.xlen(stream)

    async def stream_trim(self, stream: str, maxlen: int) -> int:
        """Trim a stream to a maximum length."""
        return await self._redis.xtrim(stream, maxlen=maxlen)

    # --- Pub/Sub ---

    async def publish(self, channel: str, message: str) -> int:
        """Publish a message to a channel."""
        return await self._redis.publish(channel, message)

    # --- Key/Value ---

    async def get(self, key: str) -> str | None:
        return await self._redis.get(key)

    async def set(
        self, key: str, value: str, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        return await self._redis.set(key, value, ex=ex, nx=nx)

    async def delete(self, *keys: str) -> int:
        return await self._redis.delete(*keys)

    # --- Lifecycle ---

    async def ping(self) -> bool:
        """Health check."""
        return await self._redis.ping()

    async def close(self) -> None:
        """Close the connection pool."""
        await self._redis.aclose()
        await self._pool.aclose()
