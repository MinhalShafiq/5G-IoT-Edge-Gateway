"""Batch Analytics — FastAPI application entry point.

This cloud-side service runs PySpark jobs for historical analysis on large
sensor datasets, providing aggregation, anomaly reports, device health
scoring, and trend analysis.
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import structlog
from fastapi import FastAPI

from shared.database.postgres import init_db, close_db
from shared.database.redis_client import RedisClient
from shared.observability.logging_config import setup_logging

from batch_analytics.config import Settings
from batch_analytics.routers import health, jobs, results
from batch_analytics.services.spark_manager import SparkManager
from batch_analytics.services.result_store import ResultStore
from batch_analytics.services.job_runner import JobRunner

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle: startup and shutdown."""
    settings = Settings()

    # --- Startup ---
    setup_logging(settings.service_name, settings.log_level)
    logger.info(
        "starting batch-analytics",
        http_port=settings.http_port,
        spark_master=settings.spark_master,
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

    # Initialize Spark manager (lazy session creation)
    spark_manager = SparkManager(settings)
    app.state.spark_manager = spark_manager
    logger.info("spark manager initialized", master=settings.spark_master)

    # Initialize result store and job runner
    result_store = ResultStore()
    app.state.result_store = result_store

    job_runner = JobRunner(spark_manager, result_store)
    app.state.job_runner = job_runner
    logger.info("job runner initialized")

    yield

    # --- Shutdown ---
    logger.info("shutting down batch-analytics")

    spark_manager.stop()
    logger.info("spark session stopped")

    await redis_client.close()
    logger.info("redis client closed")

    await close_db()
    logger.info("postgresql connections closed")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="IoT Edge Gateway — Batch Analytics",
        description="PySpark-based batch analytics for historical sensor data analysis",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Mount routers
    api_prefix = "/api/v1"
    app.include_router(health.router)
    app.include_router(jobs.router, prefix=api_prefix)
    app.include_router(results.router, prefix=api_prefix)

    return app


app = create_app()


def run() -> None:
    """Entry point for the batch-analytics console script."""
    import uvicorn

    settings = Settings()
    uvicorn.run(
        "batch_analytics.main:app",
        host="0.0.0.0",
        port=settings.http_port,
        reload=settings.environment == "development",
    )
