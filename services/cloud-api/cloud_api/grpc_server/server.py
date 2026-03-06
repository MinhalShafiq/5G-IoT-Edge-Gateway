"""gRPC server setup for the Cloud API Gateway.

Creates an async gRPC server that listens on the configured port and serves
the TelemetryService (and any future services). The server is started as a
background asyncio task from the FastAPI lifespan and stopped on shutdown.
"""

from __future__ import annotations

import structlog
import grpc
from grpc import aio as grpc_aio

from cloud_api.config import Settings
from cloud_api.grpc_server.telemetry_servicer import TelemetryServicer
from shared.database.redis_client import RedisClient

logger = structlog.get_logger(__name__)

# Module-level reference so ``stop_grpc_server`` can reach it.
_server: grpc_aio.Server | None = None


async def start_grpc_server(
    settings: Settings,
    redis_client: RedisClient,
) -> None:
    """Create, configure, and start the async gRPC server.

    This coroutine blocks (via ``server.wait_for_termination()``) until the
    server is explicitly stopped — it is designed to be wrapped in an
    ``asyncio.create_task``.
    """
    global _server

    _server = grpc_aio.server()

    # Register service implementations
    servicer = TelemetryServicer(redis_client=redis_client, settings=settings)

    # -----------------------------------------------------------------
    # When proto stubs are generated, wire up the servicer like this:
    #
    #   from shared.proto_gen.gateway.v1 import telemetry_pb2_grpc
    #   telemetry_pb2_grpc.add_TelemetryServiceServicer_to_server(servicer, _server)
    #
    # For now we just log that stubs are pending.
    # -----------------------------------------------------------------
    logger.info(
        "grpc servicer registered (stubs pending proto generation)",
        servicer=servicer.__class__.__name__,
    )

    listen_addr = f"[::]:{settings.grpc_port}"
    _server.add_insecure_port(listen_addr)

    await _server.start()
    logger.info("grpc server started", address=listen_addr)

    # Block until termination
    await _server.wait_for_termination()


async def stop_grpc_server(grace_period: float = 5.0) -> None:
    """Gracefully stop the running gRPC server."""
    global _server
    if _server is not None:
        logger.info("stopping grpc server", grace_period=grace_period)
        await _server.stop(grace=grace_period)
        _server = None
        logger.info("grpc server stopped")
