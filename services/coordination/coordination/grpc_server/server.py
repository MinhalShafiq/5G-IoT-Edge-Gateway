"""gRPC async server setup for the Coordination service.

Wires up the CoordinationServicer and binds to the configured port.
Once proto stubs are generated the servicer will be registered with
``add_CoordinationServicer_to_server``.
"""

from __future__ import annotations

import grpc
from grpc import aio as grpc_aio

from shared.observability.logging_config import get_logger

from coordination.grpc_server.coordination_servicer import CoordinationServicer
from coordination.services.config_sync import ConfigSync
from coordination.services.leader_election import LeaderElection
from coordination.state.cluster_state import ClusterState

logger = get_logger(__name__)


class GrpcServer:
    """Manages the lifecycle of the async gRPC server."""

    def __init__(
        self,
        port: int,
        cluster_state: ClusterState,
        leader_election: LeaderElection,
        config_sync: ConfigSync,
    ) -> None:
        self._port = port
        self._cluster_state = cluster_state
        self._leader_election = leader_election
        self._config_sync = config_sync
        self._server: grpc_aio.Server | None = None

    async def start(self) -> None:
        """Create, configure and start the gRPC server."""
        self._server = grpc_aio.server()

        # Instantiate the servicer
        servicer = CoordinationServicer(
            cluster_state=self._cluster_state,
            leader_election=self._leader_election,
            config_sync=self._config_sync,
        )

        # ------------------------------------------------------------------
        # Proto stub registration placeholder
        # ------------------------------------------------------------------
        # When proto stubs are generated, register the servicer like:
        #   coordination_pb2_grpc.add_CoordinationServicer_to_server(
        #       servicer, self._server
        #   )
        #
        # For now we attach the servicer so it is accessible for testing.
        self._servicer = servicer

        bind_address = f"[::]:{self._port}"
        self._server.add_insecure_port(bind_address)
        await self._server.start()
        logger.info("grpc_server_listening", address=bind_address)

    async def stop(self, grace: float = 5.0) -> None:
        """Gracefully stop the gRPC server."""
        if self._server is not None:
            await self._server.stop(grace=grace)
            logger.info("grpc_server_stopped")

    async def wait_for_termination(self) -> None:
        """Block until the server terminates."""
        if self._server is not None:
            await self._server.wait_for_termination()
