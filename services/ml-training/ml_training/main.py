"""ML Training service — FastAPI application entry point.

Cloud-side service that trains PyTorch models on aggregated sensor data
and exports them to ONNX format for edge deployment.
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import structlog
from fastapi import FastAPI

from shared.database.postgres import init_db, close_db
from shared.database.redis_client import RedisClient
from shared.observability.logging_config import setup_logging

from ml_training.config import Settings
from ml_training.routers import health, training_jobs, experiments
from ml_training.services.trainer import Trainer
from ml_training.services.data_loader import TelemetryDataLoader
from ml_training.services.model_exporter import ModelExporter
from ml_training.services.experiment_tracker import ExperimentTracker

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle: startup and shutdown."""
    settings = Settings()

    # --- Startup ---
    setup_logging(settings.service_name, settings.log_level)
    logger.info(
        "starting ml-training",
        http_port=settings.http_port,
        device=settings.device,
    )

    # Initialize Redis client
    redis_client = RedisClient(
        url=settings.redis_url,
        max_connections=settings.redis_max_connections,
    )
    app.state.redis = redis_client
    logger.info("redis client initialized")

    # Initialize PostgreSQL (create tables if missing)
    await init_db(settings.postgres_dsn)
    logger.info("postgresql initialized")

    # Store settings on app state for dependency injection
    app.state.settings = settings

    # Initialize service components
    experiment_tracker = ExperimentTracker()
    app.state.experiment_tracker = experiment_tracker

    data_loader = TelemetryDataLoader(settings.postgres_dsn)
    app.state.data_loader = data_loader

    model_exporter = ModelExporter(
        registry_url=settings.model_registry_url,
        output_dir=settings.model_output_dir,
    )
    app.state.model_exporter = model_exporter

    trainer = Trainer(
        settings=settings,
        data_loader=data_loader,
        model_exporter=model_exporter,
        experiment_tracker=experiment_tracker,
    )
    app.state.trainer = trainer
    logger.info("training engine initialized", device=settings.device)

    yield

    # --- Shutdown ---
    logger.info("shutting down ml-training")

    await redis_client.close()
    logger.info("redis client closed")

    await close_db()
    logger.info("postgresql connections closed")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="IoT Edge Gateway — ML Training",
        description="Cloud-side ML training service: trains PyTorch models on aggregated sensor data and exports to ONNX",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Mount routers under /api/v1/
    api_prefix = "/api/v1"
    app.include_router(health.router)
    app.include_router(training_jobs.router, prefix=api_prefix)
    app.include_router(experiments.router, prefix=api_prefix)

    return app


app = create_app()


def run() -> None:
    """Entry point for the ml-training console script."""
    import uvicorn

    settings = Settings()
    uvicorn.run(
        "ml_training.main:app",
        host="0.0.0.0",
        port=settings.http_port,
        reload=settings.environment == "development",
    )
