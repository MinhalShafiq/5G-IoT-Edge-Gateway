"""Worker that reads from Redis Stream and batch-writes to PostgreSQL."""

import asyncio
import time

from shared.database.redis_client import RedisClient
from shared.database.postgres import get_async_engine, get_session_factory
from shared.models.telemetry import TelemetryReading
from shared.streams.constants import StreamName, ConsumerGroup
from shared.observability.logging_config import get_logger
from shared.observability.metrics import STREAM_MESSAGES_CONSUMED

from data_persistence.config import Settings
from data_persistence.db.models import TelemetryRecord
from data_persistence.services.batch_writer import BatchWriter

logger = get_logger(__name__)


def _telemetry_to_record(reading: TelemetryReading) -> TelemetryRecord:
    """Convert a TelemetryReading pydantic model to a TelemetryRecord ORM instance."""
    return TelemetryRecord(
        device_id=reading.device_id,
        device_type=reading.device_type,
        timestamp=reading.timestamp,
        payload=reading.payload,
        metadata_=reading.metadata,
    )


async def stream_to_postgres(
    redis_client: RedisClient,
    settings: Settings,
    shutdown_event: asyncio.Event,
) -> None:
    """Main worker loop: read from Redis Stream and batch-write to PostgreSQL.

    Reads messages from the "raw_telemetry" stream as part of the
    "data_persistence" consumer group. Messages are accumulated in a
    BatchWriter buffer and flushed to PostgreSQL when either:
      - The batch reaches batch_size, or
      - flush_interval_seconds have elapsed since the last flush.

    After a successful flush, consumed messages are acknowledged in Redis.
    On failure, messages remain unacknowledged and will be redelivered.

    Args:
        redis_client: Initialized async Redis client.
        settings: Service configuration.
        shutdown_event: Event that signals graceful shutdown.
    """
    stream = StreamName.RAW_TELEMETRY
    group = ConsumerGroup.DATA_PERSISTENCE
    consumer = settings.consumer_name

    engine = get_async_engine(settings.postgres_dsn)
    session_factory = get_session_factory(engine)

    batch_writer = BatchWriter()
    pending_ids: list[str] = []  # Stream entry IDs awaiting acknowledgement
    last_flush_time = time.monotonic()

    logger.info(
        "worker_started",
        stream=stream,
        group=group,
        consumer=consumer,
        batch_size=settings.batch_size,
        flush_interval=settings.flush_interval_seconds,
    )

    while not shutdown_event.is_set():
        try:
            # Read new messages from the stream
            entries = await redis_client.stream_read_group(
                stream=stream,
                group=group,
                consumer=consumer,
                count=settings.batch_size,
                block_ms=1000,  # Block for 1s max so we can check shutdown and flush timer
            )

            # Process each message
            for entry_id, data in entries:
                try:
                    reading = TelemetryReading.from_stream_dict(data)
                    record = _telemetry_to_record(reading)
                    batch_writer.accumulate(record)
                    pending_ids.append(entry_id)

                    STREAM_MESSAGES_CONSUMED.labels(
                        stream=stream, consumer_group=group
                    ).inc()

                except Exception as e:
                    logger.error(
                        "message_parse_error",
                        entry_id=entry_id,
                        error=str(e),
                        data=data,
                    )
                    # Acknowledge bad messages to avoid infinite redelivery
                    await redis_client.stream_ack(stream, group, entry_id)
                    continue

            # Decide whether to flush
            elapsed = time.monotonic() - last_flush_time
            should_flush = (
                batch_writer.size >= settings.batch_size
                or (batch_writer.size > 0 and elapsed >= settings.flush_interval_seconds)
            )

            if should_flush:
                try:
                    async with session_factory() as session:
                        count = await batch_writer.flush(session)

                    # Acknowledge all successfully written messages
                    if pending_ids:
                        await redis_client.stream_ack(stream, group, *pending_ids)
                        logger.info(
                            "messages_acknowledged",
                            count=len(pending_ids),
                        )
                        pending_ids.clear()

                    last_flush_time = time.monotonic()

                except Exception as e:
                    logger.error(
                        "flush_error",
                        error=str(e),
                        buffered_count=batch_writer.size,
                    )
                    # Do NOT clear pending_ids or the buffer; they will be
                    # retried on the next flush cycle. The batch_writer buffer
                    # is intact because flush() only clears on success.

        except asyncio.CancelledError:
            logger.info("worker_cancelled")
            break
        except Exception as e:
            logger.error("worker_loop_error", error=str(e))
            # Brief pause before retrying to avoid tight error loops
            await asyncio.sleep(1.0)

    # Graceful shutdown: flush any remaining buffered records
    if batch_writer.size > 0:
        logger.info("shutdown_flush", buffered_count=batch_writer.size)
        try:
            async with session_factory() as session:
                await batch_writer.flush(session)
            if pending_ids:
                await redis_client.stream_ack(stream, group, *pending_ids)
                pending_ids.clear()
        except Exception as e:
            logger.error("shutdown_flush_error", error=str(e))

    logger.info("worker_stopped")
