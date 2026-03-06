"""HTTP ingestion router — alternative to MQTT for telemetry submission."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.models.telemetry import TelemetryReading
from shared.observability.logging_config import get_logger
from shared.streams.constants import StreamName

from data_ingestion.services import ingestion_service

logger = get_logger(__name__)

router = APIRouter()


class IngestResponse(BaseModel):
    """Response returned after successful ingestion."""

    status: str = "accepted"
    stream_id: str
    device_id: str


class StreamStats(BaseModel):
    """Basic statistics about the raw telemetry stream."""

    stream_name: str
    stream_length: int


@router.post("/telemetry", response_model=IngestResponse, status_code=202)
async def ingest_telemetry(reading: TelemetryReading, request: Request):
    """Receive a telemetry reading via HTTP POST and publish to Redis Stream.

    Validates the incoming JSON payload against the TelemetryReading schema,
    then publishes it to the ``raw_telemetry`` stream for downstream consumers.
    """
    redis_client = request.app.state.redis_client
    settings = request.app.state.settings

    try:
        stream_id = await ingestion_service.process(
            reading=reading,
            redis_client=redis_client,
            protocol="http",
            stream_max_len=settings.stream_max_len,
        )
    except Exception as exc:
        logger.error(
            "http_ingestion_failed",
            device_id=str(reading.device_id),
            error=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to ingest telemetry: {exc}",
        ) from exc

    return IngestResponse(
        status="accepted",
        stream_id=stream_id,
        device_id=str(reading.device_id),
    )


@router.get("/telemetry/stats", response_model=StreamStats)
async def telemetry_stats(request: Request):
    """Return the current length of the raw_telemetry stream."""
    redis_client = request.app.state.redis_client

    try:
        length = await redis_client.stream_len(StreamName.RAW_TELEMETRY)
    except Exception as exc:
        logger.error("stats_fetch_failed", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch stream stats: {exc}",
        ) from exc

    return StreamStats(
        stream_name=StreamName.RAW_TELEMETRY,
        stream_length=length,
    )
