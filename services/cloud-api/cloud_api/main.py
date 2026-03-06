"""Cloud API Gateway — FastAPI application entry point.

This is the cloud-side entry point that receives telemetry from edge gateways,
serves as the model registry, and provides aggregated analytics APIs.
"""

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import structlog
from fastapi import FastAPI

from shared.database.postgres import init_db, close_db
from shared.database.redis_client import RedisClient
from shared.observability.logging_config import setup_logging

from cloud_api.config import Settings
from cloud_api.grpc_server.server import start_grpc_server, stop_grpc_server
from cloud_api.routers import health, telemetry, models, devices, analytics

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle: startup and shutdown."""
    settings = Settings()

    # --- Startup ---
    setup_logging(settings.service_name, settings.log_level)
    logger.info(
        "starting cloud-api",
        http_port=settings.http_port,
        grpc_port=settings.grpc_port,
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

    # Start gRPC server as a background task
    grpc_task = asyncio.create_task(
        start_grpc_server(settings, redis_client)
    )
    app.state.grpc_task = grpc_task
    logger.info("grpc server starting", port=settings.grpc_port)

    yield

    # --- Shutdown ---
    logger.info("shutting down cloud-api")

    await stop_grpc_server()
    grpc_task.cancel()
    try:
        await grpc_task
    except asyncio.CancelledError:
        pass

    await redis_client.close()
    logger.info("redis client closed")

    await close_db()
    logger.info("postgresql connections closed")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="IoT Edge Gateway — Cloud API",
        description="Cloud-side API for telemetry ingestion, model registry, and analytics",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Mount routers under /api/v1/
    api_prefix = "/api/v1"
    app.include_router(health.router)
    app.include_router(telemetry.router, prefix=api_prefix)
    app.include_router(models.router, prefix=api_prefix)
    app.include_router(devices.router, prefix=api_prefix)
    app.include_router(analytics.router, prefix=api_prefix)

    return app


app = create_app()


def run() -> None:
    """Entry point for the cloud-api console script."""
    import uvicorn

    settings = Settings()
    uvicorn.run(
        "cloud_api.main:app",
        host="0.0.0.0",
        port=settings.http_port,
        reload=settings.environment == "development",
    )
