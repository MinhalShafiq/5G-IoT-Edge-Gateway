"""Periodic gauges for Grafana: active devices, stream lengths, consumer lag.

``record_device`` is called on every ingested reading (a dict write, so it
is cheap on the hot path); ``run_stream_monitor`` turns that into the
``active_devices`` gauge and polls Redis for stream/consumer-group state.
"""

import asyncio
import time
from collections import Counter

from shared.database.redis_client import RedisClient
from shared.observability.logging_config import get_logger
from shared.observability.metrics import (
    ACTIVE_DEVICES,
    CONSUMER_GROUP_LAG,
    CONSUMER_GROUP_PENDING,
    STREAM_LENGTH,
)
from shared.streams.constants import StreamName

logger = get_logger(__name__)

# A device counts as active if it reported within this window.
ACTIVE_WINDOW_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 5.0

# device_id -> (device_type, last_seen monotonic time)
_last_seen: dict[str, tuple[str, float]] = {}
# Every type ever seen, so a type that goes quiet is reported as 0.
_known_types: set[str] = set()


def record_device(device_id: str, device_type: str) -> None:
    """Mark a device as having just sent telemetry."""
    _last_seen[device_id] = (device_type, time.monotonic())


def _update_active_devices() -> None:
    cutoff = time.monotonic() - ACTIVE_WINDOW_SECONDS
    stale = [d for d, (_, seen) in _last_seen.items() if seen < cutoff]
    for device_id in stale:
        del _last_seen[device_id]

    counts = Counter(device_type for device_type, _ in _last_seen.values())
    _known_types.update(counts)
    for device_type in _known_types:
        ACTIVE_DEVICES.labels(device_type=device_type).set(counts.get(device_type, 0))


async def _update_stream_gauges(redis_client: RedisClient) -> None:
    for stream in (StreamName.RAW_TELEMETRY, StreamName.ALERTS):
        if not await redis_client.redis.exists(stream):
            continue
        STREAM_LENGTH.labels(stream=stream).set(await redis_client.stream_len(stream))
        for group in await redis_client.redis.xinfo_groups(stream):
            # A group no consumer has ever joined (e.g. alert_handler when the
            # coordination service isn't deployed) has no lag to report: its
            # "lag" is just the stream length. A crashed consumer stays
            # registered, so a stalled group is still reported.
            if group["consumers"] == 0:
                continue
            CONSUMER_GROUP_LAG.labels(stream=stream, consumer_group=group["name"]).set(
                group.get("lag") or 0
            )
            CONSUMER_GROUP_PENDING.labels(
                stream=stream, consumer_group=group["name"]
            ).set(group["pending"])


async def run_stream_monitor(redis_client: RedisClient) -> None:
    """Refresh the gauges every POLL_INTERVAL_SECONDS until cancelled."""
    while True:
        try:
            _update_active_devices()
            await _update_stream_gauges(redis_client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("stream_monitor_failed", error=str(exc))
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
