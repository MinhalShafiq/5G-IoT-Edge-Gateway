"""gRPC TelemetryService implementation.

Handles telemetry data received from edge gateways over gRPC, supporting both
unary (batch) and streaming modes. Incoming readings are persisted to
PostgreSQL via the telemetry service layer.

NOTE: Proto-generated stubs are not yet compiled. Once
``scripts/generate-protos.sh`` has been run, uncomment the stub imports
and have this class inherit from the generated base servicer.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

import structlog

from shared.database.redis_client import RedisClient
from shared.database.postgres import get_async_session
from shared.models.telemetry import TelemetryReading
from cloud_api.config import Settings
from cloud_api.services.telemetry_service import store_batch

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Placeholder until proto stubs are generated:
#
#   from shared.proto_gen.gateway.v1 import telemetry_pb2, telemetry_pb2_grpc
#
# After generation, update the class declaration to:
#   class TelemetryServicer(telemetry_pb2_grpc.TelemetryServiceServicer):
# ---------------------------------------------------------------------------


class TelemetryServicer:
    """gRPC Telemetry Service implementation.

    Wire up with generated stubs after running ``scripts/generate-protos.sh``.
    """

    def __init__(self, redis_client: RedisClient, settings: Settings) -> None:
        self.redis_client = redis_client
        self.settings = settings

    # ------------------------------------------------------------------
    # Unary RPC
    # ------------------------------------------------------------------

    async def SendTelemetry(self, request, context):
        """Receive a TelemetryBatch from an edge gateway.

        Deserializes each reading, stores the batch in PostgreSQL, and
        returns the number of accepted readings.

        Once proto stubs are available the ``request`` will be a
        ``TelemetryBatch`` proto message; until then this method serves
        as the reference implementation.
        """
        logger.info("grpc SendTelemetry called")

        try:
            # Convert proto-like request into shared Pydantic models.
            # With real stubs this would iterate request.readings.
            readings: list[TelemetryReading] = []

            # Example deserialization (replace with proto field access):
            for raw in getattr(request, "readings", []):
                reading = TelemetryReading(
                    device_id=UUID(raw.device_id),
                    device_type=raw.device_type,
                    timestamp=datetime.fromisoformat(raw.timestamp),
                    payload=json.loads(raw.payload) if isinstance(raw.payload, str) else raw.payload,
                    metadata=json.loads(raw.metadata) if isinstance(raw.metadata, str) else raw.metadata,
                )
                readings.append(reading)

            # Persist to PostgreSQL
            async for session in get_async_session(self.settings.postgres_dsn):
                accepted = await store_batch(readings, session)

            logger.info("grpc batch stored", accepted=accepted)

            # Return value — will be a proto response once stubs exist.
            # For now return a simple object with an ``accepted`` attribute.
            return type("SendTelemetryResponse", (), {"accepted_count": accepted})()

        except Exception as exc:
            logger.error("grpc SendTelemetry error", error=str(exc))
            # In production, set a gRPC error code on the context:
            # context.set_code(grpc.StatusCode.INTERNAL)
            # context.set_details(str(exc))
            raise

    # ------------------------------------------------------------------
    # Streaming RPC
    # ------------------------------------------------------------------

    async def StreamTelemetry(self, request_iterator, context):
        """Receive a client-side stream of individual telemetry readings.

        Each message in the stream is processed and stored as it arrives.
        Returns a summary response when the stream completes.
        """
        logger.info("grpc StreamTelemetry started")
        total_accepted = 0

        try:
            async for request in request_iterator:
                readings: list[TelemetryReading] = []

                for raw in getattr(request, "readings", [request]):
                    reading = TelemetryReading(
                        device_id=UUID(raw.device_id) if hasattr(raw, "device_id") else UUID(str(raw.get("device_id", ""))),
                        device_type=getattr(raw, "device_type", "unknown"),
                        timestamp=datetime.fromisoformat(
                            getattr(raw, "timestamp", datetime.utcnow().isoformat())
                        ),
                        payload=json.loads(raw.payload) if isinstance(getattr(raw, "payload", {}), str) else getattr(raw, "payload", {}),
                        metadata=json.loads(raw.metadata) if isinstance(getattr(raw, "metadata", {}), str) else getattr(raw, "metadata", {}),
                    )
                    readings.append(reading)

                if readings:
                    async for session in get_async_session(self.settings.postgres_dsn):
                        accepted = await store_batch(readings, session)
                    total_accepted += accepted

            logger.info("grpc StreamTelemetry completed", total_accepted=total_accepted)
            return type("StreamTelemetryResponse", (), {"accepted_count": total_accepted})()

        except Exception as exc:
            logger.error("grpc StreamTelemetry error", error=str(exc))
            raise
