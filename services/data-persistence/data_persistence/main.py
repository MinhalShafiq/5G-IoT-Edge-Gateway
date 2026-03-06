"""Data Persistence Worker entrypoint.

Standalone async worker (no HTTP server) that consumes telemetry readings
from the raw_telemetry Redis Stream and batch-writes them to PostgreSQL.
"""

import asyncio
import signal

from data_persistence.config import settings
from shared.observability.logging_config import setup_logging, get_logger
from shared.database.redis_client import RedisClient
from shared.database.postgres import init_db, close_db
from shared.streams.constants import StreamName, ConsumerGroup

# Ensure the telemetry_readings model is registered with Base.metadata
# before init_db() calls create_all.
import data_persistence.db.models  # noqa: F401

from data_persistence.workers.stream_to_postgres import stream_to_postgres

logger = get_logger(__name__)


async def main() -> None:
    """Initialize resources and run the stream-to-postgres worker."""

    # --- Logging ---
    setup_logging(
        service_name=settings.service_name,
        log_level=settings.log_level,
    )

    logger.info(
        "starting",
        service=settings.service_name,
        environment=settings.environment,
        batch_size=settings.batch_size,
        flush_interval=settings.flush_interval_seconds,
        consumer=settings.consumer_name,
    )

    # --- Redis ---
    redis_client = RedisClient(
        url=settings.redis_url,
        max_connections=settings.redis_max_connections,
    )

    # --- PostgreSQL ---
    logger.info("initializing_database")
    await init_db(settings.postgres_dsn)

    # --- Ensure consumer group ---
    logger.info(
        "ensuring_consumer_group",
        stream=StreamName.RAW_TELEMETRY,
        group=ConsumerGroup.DATA_PERSISTENCE,
    )
    await redis_client.ensure_consumer_group(
        stream=StreamName.RAW_TELEMETRY,
        group=ConsumerGroup.DATA_PERSISTENCE,
    )

    # --- Shutdown event ---
    shutdown_event = asyncio.Event()

    def _signal_handler(sig: signal.Signals) -> None:
        logger.info("shutdown_signal_received", signal=sig.name)
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler, sig)
        except NotImplementedError:
            # Windows does not support add_signal_handler; fall back to
            # signal.signal which works for SIGINT (Ctrl+C).
            signal.signal(sig, lambda s, f: _signal_handler(signal.Signals(s)))

    # --- Run worker ---
    try:
        await stream_to_postgres(
            redis_client=redis_client,
            settings=settings,
            shutdown_event=shutdown_event,
        )
    finally:
        # --- Cleanup ---
        logger.info("shutting_down")
        await redis_client.close()
        await close_db()
        logger.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
