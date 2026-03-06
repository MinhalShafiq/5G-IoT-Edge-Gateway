"""Data Ingestion service — FastAPI application entry point.

Subscribes to MQTT topics, receives telemetry from IoT devices,
and publishes to Redis Streams for downstream processing.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.database.redis_client import RedisClient
from shared.observability.logging_config import setup_logging, get_logger
from shared.observability.metrics import SERVICE_INFO
from shared.streams.constants import StreamName, ConsumerGroup
from shared.utils.health import create_health_router

from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from data_ingestion.config import Settings
from data_ingestion.routers.ingest import router as ingest_router
from data_ingestion.services.mqtt_subscriber import MQTTSubscriber

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: startup and shutdown."""
    settings = Settings()

    # Configure structured logging
    setup_logging(settings.service_name, settings.log_level)

    logger.info(
        "starting_data_ingestion_service",
        mqtt_broker=settings.mqtt_broker_host,
        mqtt_port=settings.mqtt_broker_port,
        mqtt_topics=settings.mqtt_topics,
    )

    # Publish service metadata to Prometheus
    SERVICE_INFO.info(
        {
            "service_name": settings.service_name,
            "version": "0.1.0",
            "environment": settings.environment,
        }
    )

    # Initialize Redis client
    redis_client = RedisClient(
        url=settings.redis_url,
        max_connections=settings.redis_max_connections,
    )

    # Ensure consumer groups exist for downstream services
    await redis_client.ensure_consumer_group(
        StreamName.RAW_TELEMETRY, ConsumerGroup.ML_INFERENCE
    )
    await redis_client.ensure_consumer_group(
        StreamName.RAW_TELEMETRY, ConsumerGroup.DATA_PERSISTENCE
    )
    await redis_client.ensure_consumer_group(
        StreamName.RAW_TELEMETRY, ConsumerGroup.ALERT_HANDLER
    )

    logger.info("redis_connected", redis_url=settings.redis_url)

    # Store in app.state for access by routers
    app.state.redis_client = redis_client
    app.state.settings = settings

    # Start the MQTT subscriber as a background task
    mqtt_subscriber = MQTTSubscriber(
        settings=settings,
        redis_client=redis_client,
    )
    app.state.mqtt_subscriber = mqtt_subscriber
    mqtt_task = asyncio.create_task(mqtt_subscriber.run())
    app.state.mqtt_task = mqtt_task

    logger.info("data_ingestion_service_started")

    yield

    # --- Shutdown ---
    logger.info("shutting_down_data_ingestion_service")

    # Stop MQTT subscriber gracefully
    mqtt_subscriber.stop()
    mqtt_task.cancel()
    try:
        await mqtt_task
    except asyncio.CancelledError:
        pass

    # Close Redis connection
    await redis_client.close()

    logger.info("data_ingestion_service_stopped")


app = FastAPI(
    title="IoT Edge Gateway — Data Ingestion",
    description="Receives telemetry from IoT devices via MQTT/HTTP and publishes to Redis Streams.",
    version="0.1.0",
    lifespan=lifespan,
)

# Register routers
app.include_router(create_health_router("data-ingestion"))
app.include_router(ingest_router, prefix="/api/v1", tags=["ingestion"])


@app.get("/metrics")
async def metrics():
    """Expose Prometheus metrics."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
