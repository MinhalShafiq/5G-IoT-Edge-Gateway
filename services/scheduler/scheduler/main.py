"""Resource Scheduler — FastAPI + gRPC dual-server entry point.

Runs the HTTP API for task submission and resource reporting alongside a gRPC
server for high-performance inter-service communication.  A background scheduling
loop continuously evaluates queued tasks against live resource data.
"""

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from concurrent import futures

import grpc
import structlog
import uvicorn
from fastapi import FastAPI

from shared.database.redis_client import RedisClient
from shared.observability.logging_config import setup_logging

from scheduler.config import Settings
from scheduler.routers import health, tasks, resources
from scheduler.services.resource_monitor import ResourceMonitor
from scheduler.services.scheduler_engine import SchedulerEngine
from scheduler.services.adaptive_scheduler import AdaptiveScheduler
from scheduler.policies.latency_first import LatencyFirstPolicy
from scheduler.policies.cost_aware import CostAwarePolicy
from scheduler.policies.balanced import BalancedPolicy

logger = structlog.get_logger(__name__)

# Module-level gRPC server reference for clean shutdown
_grpc_server: grpc.aio.Server | None = None


async def start_grpc_server(settings: Settings) -> None:
    """Start the async gRPC server on the configured port."""
    global _grpc_server
    _grpc_server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=4))
    listen_addr = f"[::]:{settings.grpc_port}"
    _grpc_server.add_insecure_port(listen_addr)
    await _grpc_server.start()
    logger.info("grpc server started", port=settings.grpc_port)
    await _grpc_server.wait_for_termination()


async def stop_grpc_server() -> None:
    """Gracefully stop the gRPC server."""
    global _grpc_server
    if _grpc_server is not None:
        await _grpc_server.stop(grace=5)
        logger.info("grpc server stopped")
        _grpc_server = None


async def _scheduling_loop(
    engine: SchedulerEngine, interval: float
) -> None:
    """Background loop that drains the task queue at a fixed interval."""
    logger.info("scheduling loop started", interval_seconds=interval)
    while True:
        try:
            await engine.drain_queue()
        except Exception:
            logger.exception("error in scheduling loop")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle: startup and shutdown."""
    settings = Settings()

    # --- Startup ---
    setup_logging(settings.service_name, settings.log_level)
    logger.info(
        "starting scheduler",
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

    # Store settings on app state for dependency injection
    app.state.settings = settings

    # Initialize the resource monitor
    resource_monitor = ResourceMonitor(
        stale_threshold=settings.resource_stale_threshold_seconds,
    )
    app.state.resource_monitor = resource_monitor

    # Assemble placement policies
    policies = [
        LatencyFirstPolicy(edge_latency_sla_ms=settings.edge_latency_sla_ms),
        CostAwarePolicy(cloud_endpoint=settings.cloud_endpoint),
        BalancedPolicy(edge_latency_sla_ms=settings.edge_latency_sla_ms),
    ]

    # Initialize the adaptive scheduler (feedback-based)
    adaptive_scheduler = AdaptiveScheduler(
        resource_monitor=resource_monitor,
        edge_latency_sla_ms=settings.edge_latency_sla_ms,
        cloud_endpoint=settings.cloud_endpoint,
    )
    app.state.adaptive_scheduler = adaptive_scheduler

    # Build the core scheduling engine
    engine = SchedulerEngine(
        resource_monitor=resource_monitor,
        policies=policies,
    )
    app.state.scheduler_engine = engine
    logger.info("scheduler engine initialized", policy_count=len(policies))

    # Start gRPC server as a background task
    grpc_task = asyncio.create_task(start_grpc_server(settings))
    app.state.grpc_task = grpc_task

    # Start the scheduling loop as a background task
    scheduling_task = asyncio.create_task(
        _scheduling_loop(engine, settings.scheduling_interval_seconds)
    )
    app.state.scheduling_task = scheduling_task

    yield

    # --- Shutdown ---
    logger.info("shutting down scheduler")

    # Cancel the scheduling loop
    scheduling_task.cancel()
    try:
        await scheduling_task
    except asyncio.CancelledError:
        pass

    # Stop gRPC
    await stop_grpc_server()
    grpc_task.cancel()
    try:
        await grpc_task
    except asyncio.CancelledError:
        pass

    # Close Redis
    await redis_client.close()
    logger.info("redis client closed")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="IoT Edge Gateway — Resource Scheduler",
        description="Decides whether ML inference tasks run on edge or cloud based on resource availability, latency, and cost",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Mount routers
    api_prefix = "/api/v1"
    app.include_router(health.router)
    app.include_router(tasks.router, prefix=api_prefix)
    app.include_router(resources.router, prefix=api_prefix)

    return app


app = create_app()


def run() -> None:
    """Entry point for the scheduler console script."""
    settings = Settings()
    uvicorn.run(
        "scheduler.main:app",
        host="0.0.0.0",
        port=settings.http_port,
        reload=settings.environment == "development",
    )


if __name__ == "__main__":
    run()
