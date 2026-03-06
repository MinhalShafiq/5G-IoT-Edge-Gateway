"""Device Manager service -- FastAPI application entry point.

Manages the full device lifecycle: registration, CRUD operations,
provisioning workflows, and firmware/OTA management.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.database.postgres import init_db, close_db
from shared.database.redis_client import RedisClient
from shared.observability.logging_config import setup_logging, get_logger
from shared.observability.metrics import SERVICE_INFO
from shared.utils.health import create_health_router

from device_manager.config import Settings
from device_manager.routers.devices import router as devices_router
from device_manager.routers.provisioning import router as provisioning_router
from device_manager.routers.firmware import router as firmware_router

# Import models so that Base.metadata knows about our tables before init_db
import device_manager.db.models  # noqa: F401

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: startup and shutdown."""
    settings = Settings()

    # Configure structured logging
    setup_logging(settings.service_name, settings.log_level)

    logger.info(
        "starting_device_manager_service",
        http_port=settings.http_port,
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
    logger.info("redis_connected", redis_url=settings.redis_url)

    # Initialize PostgreSQL (create tables if needed)
    await init_db(settings.postgres_dsn)
    logger.info("postgres_initialized", dsn=settings.postgres_dsn)

    # Store in app.state for access by routers and services
    app.state.redis_client = redis_client
    app.state.settings = settings

    logger.info("device_manager_service_started")

    yield

    # --- Shutdown ---
    logger.info("shutting_down_device_manager_service")

    await redis_client.close()
    await close_db()

    logger.info("device_manager_service_stopped")


app = FastAPI(
    title="IoT Edge Gateway -- Device Manager",
    description=(
        "Manages the full IoT device lifecycle: registration, CRUD, "
        "provisioning, and firmware/OTA updates."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# Register routers
app.include_router(create_health_router("device-manager"))
app.include_router(devices_router, prefix="/api/v1", tags=["devices"])
app.include_router(provisioning_router, prefix="/api/v1", tags=["provisioning"])
app.include_router(firmware_router, prefix="/api/v1", tags=["firmware"])
