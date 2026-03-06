"""Coordination service -- dual-server entry point.

Starts a FastAPI HTTP server for health endpoints and a gRPC server
for cluster coordination RPCs.  Background tasks handle leader election,
gossip-based membership, heartbeat monitoring, and config synchronisation.
"""

import asyncio
import platform
import signal
import uuid

import uvicorn
from fastapi import FastAPI

from shared.database.redis_client import RedisClient
from shared.observability.logging_config import get_logger, setup_logging
from shared.observability.metrics import SERVICE_INFO
from shared.utils.health import create_health_router

from coordination.config import Settings
from coordination.grpc_server.server import GrpcServer
from coordination.services.config_sync import ConfigSync
from coordination.services.gossip import GossipProtocol
from coordination.services.heartbeat import HeartbeatService
from coordination.services.leader_election import LeaderElection
from coordination.state.cluster_state import ClusterNode, ClusterState, NodeStatus

logger = get_logger(__name__)


def _generate_node_id() -> str:
    """Generate a stable node identifier from hostname + random suffix."""
    hostname = platform.node() or "unknown"
    short_id = uuid.uuid4().hex[:8]
    return f"{hostname}-{short_id}"


def _build_health_app(settings: Settings) -> FastAPI:
    """Create a minimal FastAPI app exposing only health / readiness endpoints."""
    app = FastAPI(
        title="IoT Edge Gateway -- Coordination",
        description="Cluster coordination, leader election, gossip membership, config sync, OTA.",
        version="0.1.0",
    )
    app.include_router(create_health_router(settings.service_name))
    return app


async def _run_http_server(app: FastAPI, host: str, port: int) -> uvicorn.Server:
    """Start uvicorn programmatically so we can shut it down gracefully."""
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    return server


async def main() -> None:  # noqa: C901 -- startup orchestration is inherently sequential
    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    settings = Settings()
    setup_logging(settings.service_name, settings.log_level)

    if not settings.node_id:
        settings.node_id = _generate_node_id()

    logger.info(
        "coordination_service_starting",
        node_id=settings.node_id,
        node_address=settings.node_address,
        grpc_port=settings.grpc_port,
        http_port=settings.http_port,
    )

    SERVICE_INFO.info(
        {
            "service_name": settings.service_name,
            "version": "0.1.0",
            "environment": settings.environment,
            "node_id": settings.node_id,
        }
    )

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------
    redis_client = RedisClient(
        url=settings.redis_url,
        max_connections=settings.redis_max_connections,
    )
    await redis_client.ping()
    logger.info("redis_connected", redis_url=settings.redis_url)

    # ------------------------------------------------------------------
    # Cluster state (in-memory)
    # ------------------------------------------------------------------
    cluster_state = ClusterState(local_node_id=settings.node_id)

    # Register self
    local_node = ClusterNode(
        node_id=settings.node_id,
        node_address=settings.node_address,
        status=NodeStatus.ALIVE,
    )
    cluster_state.add_node(local_node)

    # ------------------------------------------------------------------
    # Domain services
    # ------------------------------------------------------------------
    leader_election = LeaderElection(
        redis_client=redis_client,
        node_id=settings.node_id,
        ttl_seconds=settings.leader_lock_ttl_seconds,
    )

    config_sync = ConfigSync(
        redis_client=redis_client,
        cluster_state=cluster_state,
        node_id=settings.node_id,
    )

    gossip = GossipProtocol(
        cluster_state=cluster_state,
        node_id=settings.node_id,
        interval=settings.gossip_interval_seconds,
        fanout=settings.gossip_fanout,
    )

    heartbeat_service = HeartbeatService(
        cluster_state=cluster_state,
        node_id=settings.node_id,
        interval=settings.heartbeat_interval_seconds,
        timeout=settings.heartbeat_timeout_seconds,
    )

    # ------------------------------------------------------------------
    # gRPC server
    # ------------------------------------------------------------------
    grpc_server = GrpcServer(
        port=settings.grpc_port,
        cluster_state=cluster_state,
        leader_election=leader_election,
        config_sync=config_sync,
    )

    # ------------------------------------------------------------------
    # Graceful-shutdown plumbing
    # ------------------------------------------------------------------
    shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            # Windows does not support add_signal_handler; fall back to
            # signal.signal which is adequate for a single-threaded loop.
            signal.signal(sig, lambda _s, _f: shutdown_event.set())

    # ------------------------------------------------------------------
    # HTTP health server (uvicorn)
    # ------------------------------------------------------------------
    health_app = _build_health_app(settings)
    uvicorn_config = uvicorn.Config(
        health_app,
        host="0.0.0.0",
        port=settings.http_port,
        log_level="warning",
    )
    http_server = uvicorn.Server(uvicorn_config)

    # ------------------------------------------------------------------
    # Launch everything concurrently
    # ------------------------------------------------------------------
    async def _run_http() -> None:
        await http_server.serve()

    async def _run_grpc() -> None:
        await grpc_server.start()
        logger.info("grpc_server_started", port=settings.grpc_port)
        # Block until shutdown is requested
        await shutdown_event.wait()

    background_tasks: list[asyncio.Task[None]] = []

    try:
        # Background loops
        background_tasks.append(
            asyncio.create_task(
                leader_election.run_election_loop(
                    interval=settings.heartbeat_interval_seconds,
                    shutdown_event=shutdown_event,
                ),
                name="leader_election",
            )
        )
        background_tasks.append(
            asyncio.create_task(
                gossip.run(shutdown_event=shutdown_event),
                name="gossip",
            )
        )
        background_tasks.append(
            asyncio.create_task(
                heartbeat_service.run(shutdown_event=shutdown_event),
                name="heartbeat",
            )
        )
        background_tasks.append(
            asyncio.create_task(
                config_sync.run_sync_loop(shutdown_event=shutdown_event),
                name="config_sync",
            )
        )

        # Servers
        grpc_task = asyncio.create_task(_run_grpc(), name="grpc_server")
        http_task = asyncio.create_task(_run_http(), name="http_server")

        logger.info("coordination_service_started", node_id=settings.node_id)

        # Wait until shutdown signal
        await shutdown_event.wait()

    finally:
        # ------------------------------------------------------------------
        # Shutdown sequence
        # ------------------------------------------------------------------
        logger.info("coordination_service_shutting_down")

        # Release leader lock if we hold it
        await leader_election.release()

        # Stop background tasks
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)

        # Stop gRPC
        await grpc_server.stop()

        # Stop HTTP
        http_server.should_exit = True
        # Give uvicorn a moment to finish
        await asyncio.sleep(0.5)

        # Close Redis
        await redis_client.close()

        logger.info("coordination_service_stopped")


if __name__ == "__main__":
    asyncio.run(main())
