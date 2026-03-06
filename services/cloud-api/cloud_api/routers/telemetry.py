"""Telemetry ingestion and query endpoints.

Receives batched telemetry from edge gateways and provides historical query
and aggregation APIs backed by PostgreSQL.
"""

from datetime import datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.postgres import get_async_session
from shared.models.telemetry import TelemetryReading

from cloud_api.config import Settings, get_settings
from cloud_api.services.telemetry_service import (
    store_batch,
    query_telemetry,
    get_stats,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/telemetry", tags=["telemetry"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TelemetryBatch(BaseModel):
    """Batch of telemetry readings sent from an edge gateway."""

    gateway_id: str = ""
    readings: list[TelemetryReading]


class BatchAcceptedResponse(BaseModel):
    """Response for a successfully accepted telemetry batch."""

    accepted: int
    message: str = "batch accepted"


class TelemetryQueryResponse(BaseModel):
    """Paginated telemetry query result."""

    total: int
    limit: int
    offset: int
    readings: list[dict]


class TelemetryStatsResponse(BaseModel):
    """Aggregated telemetry statistics."""

    total_readings: int = 0
    readings_by_device_type: dict[str, int] = Field(default_factory=dict)
    earliest_reading: datetime | None = None
    latest_reading: datetime | None = None


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def get_session(
    settings: Settings = Depends(get_settings),
) -> AsyncSession:
    """Yield a database session."""
    async for session in get_async_session(settings.postgres_dsn):
        yield session


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/batch", response_model=BatchAcceptedResponse, status_code=202)
async def receive_batch(
    batch: TelemetryBatch,
    session: AsyncSession = Depends(get_session),
) -> BatchAcceptedResponse:
    """Receive a batch of telemetry readings from an edge gateway.

    Stores every reading into PostgreSQL and returns the accepted count.
    """
    logger.info(
        "telemetry batch received",
        gateway_id=batch.gateway_id,
        reading_count=len(batch.readings),
    )
    accepted = await store_batch(batch.readings, session)
    return BatchAcceptedResponse(accepted=accepted)


@router.get("/query", response_model=TelemetryQueryResponse)
async def query_telemetry_endpoint(
    device_id: UUID | None = Query(None, description="Filter by device ID"),
    device_type: str | None = Query(None, description="Filter by device type"),
    start_time: datetime | None = Query(None, description="Start of time range (ISO 8601)"),
    end_time: datetime | None = Query(None, description="End of time range (ISO 8601)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    session: AsyncSession = Depends(get_session),
) -> TelemetryQueryResponse:
    """Query historical telemetry with optional filters and pagination."""
    readings, total = await query_telemetry(
        device_id=device_id,
        start_time=start_time,
        end_time=end_time,
        device_type=device_type,
        limit=limit,
        offset=offset,
        session=session,
    )
    return TelemetryQueryResponse(
        total=total,
        limit=limit,
        offset=offset,
        readings=readings,
    )


@router.get("/stats", response_model=TelemetryStatsResponse)
async def telemetry_stats(
    session: AsyncSession = Depends(get_session),
) -> TelemetryStatsResponse:
    """Return aggregate statistics about stored telemetry."""
    stats = await get_stats(session)
    return TelemetryStatsResponse(**stats)
