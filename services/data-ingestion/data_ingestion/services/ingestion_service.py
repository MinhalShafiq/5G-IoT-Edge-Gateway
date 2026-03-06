"""Core ingestion pipeline — validates, publishes, and meters telemetry.

This module is stateless; it receives a TelemetryReading and a RedisClient
from the caller (HTTP router or MQTT subscriber) and pushes the reading to
the ``raw_telemetry`` Redis Stream.
"""

import time

from shared.database.redis_client import RedisClient
from shared.models.telemetry import TelemetryReading
from shared.observability.logging_config import get_logger
from shared.observability.metrics import (
    TELEMETRY_RECEIVED,
    STREAM_MESSAGES_PUBLISHED,
    PROCESSING_LATENCY,
)
from shared.streams.constants import StreamName

logger = get_logger(__name__)


async def process(
    reading: TelemetryReading,
    redis_client: RedisClient,
    protocol: str = "mqtt",
    stream_max_len: int = 100_000,
) -> str:
    """Validate a telemetry reading and publish it to the raw_telemetry stream.

    Args:
        reading: The validated TelemetryReading model.
        redis_client: The shared async Redis client.
        protocol: The ingestion protocol (``mqtt`` or ``http``).
        stream_max_len: Maximum length of the Redis Stream before trimming.

    Returns:
        The Redis Stream entry ID assigned to this message.
    """
    start = time.monotonic()

    # Increment the "received" counter
    TELEMETRY_RECEIVED.labels(
        device_type=reading.device_type,
        protocol=protocol,
    ).inc()

    # Serialize the reading to a flat dict suitable for XADD
    stream_data = reading.to_stream_dict()

    # Publish to Redis Stream
    stream_id = await redis_client.stream_add(
        stream=StreamName.RAW_TELEMETRY,
        data=stream_data,
        maxlen=stream_max_len,
        approximate=True,
    )

    # Increment the "published" counter
    STREAM_MESSAGES_PUBLISHED.labels(
        stream=StreamName.RAW_TELEMETRY,
    ).inc()

    # Record processing latency
    elapsed = time.monotonic() - start
    PROCESSING_LATENCY.labels(
        service="data-ingestion",
        operation="ingest",
    ).observe(elapsed)

    logger.debug(
        "telemetry_published",
        device_id=str(reading.device_id),
        device_type=reading.device_type,
        stream_id=stream_id,
        protocol=protocol,
        latency_ms=round(elapsed * 1000, 2),
    )

    return stream_id
