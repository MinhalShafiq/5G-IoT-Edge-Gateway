"""ML Inference microservice entry point.

FastAPI application with lifespan management for the ONNX inference engine,
Redis stream consumer, and model lifecycle.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.database.redis_client import RedisClient
from shared.observability.logging_config import setup_logging, get_logger
from shared.streams.constants import StreamName, ConsumerGroup
from shared.utils.health import create_health_router

from ml_inference.config import Settings
from ml_inference.services.inference_engine import InferenceEngine
from ml_inference.services.feature_extractor import FeatureExtractor
from ml_inference.services.anomaly_detector import AnomalyDetector
from ml_inference.services import model_manager
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from ml_inference.routers import inference, models

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle."""
    settings = Settings()
    setup_logging(settings.service_name, settings.log_level)

    logger.info("starting_ml_inference_service", port=settings.http_port)

    # Initialize Redis client
    redis_client = RedisClient(
        url=settings.redis_url,
        max_connections=settings.redis_max_connections,
    )

    # Initialize the ONNX inference engine
    engine = InferenceEngine()

    # Load the initial model (or generate a baseline if none exists)
    model_manager.load_initial_model(engine, settings)

    # Initialize feature extractor
    feature_extractor = FeatureExtractor(window_size=settings.feature_window_size)

    # Ensure the consumer group exists on the raw_telemetry stream
    await redis_client.ensure_consumer_group(
        StreamName.RAW_TELEMETRY, ConsumerGroup.ML_INFERENCE
    )

    # Create and start the anomaly detector as a background task
    detector = AnomalyDetector(
        redis_client=redis_client,
        inference_engine=engine,
        feature_extractor=feature_extractor,
        settings=settings,
    )
    consumer_task = asyncio.create_task(detector.run())

    # Store references on app.state for use in route handlers
    app.state.settings = settings
    app.state.redis_client = redis_client
    app.state.engine = engine
    app.state.feature_extractor = feature_extractor
    app.state.detector = detector

    logger.info("ml_inference_service_started")

    yield

    # Shutdown
    logger.info("stopping_ml_inference_service")
    detector.stop()
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    await redis_client.close()
    logger.info("ml_inference_service_stopped")


app = FastAPI(
    title="ML Inference Service",
    description="Edge-side real-time anomaly detection using ONNX Runtime",
    version="0.1.0",
    lifespan=lifespan,
)

# Include routers
app.include_router(create_health_router("ml-inference"))
app.include_router(inference.router)
app.include_router(models.router)


@app.get("/metrics")
async def metrics():
    """Expose Prometheus metrics."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
