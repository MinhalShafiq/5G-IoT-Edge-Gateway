"""Device fleet endpoints.

Provides aggregated device information that is either proxied from edge
gateways or read from locally synced data in PostgreSQL.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.postgres import get_async_session

from cloud_api.config import Settings, get_settings

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/devices", tags=["devices"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DeviceSummary(BaseModel):
    """Lightweight device representation for fleet-level listings."""

    device_id: str
    name: str
    device_type: str
    status: str
    last_seen_at: str | None = None


class DeviceStatsResponse(BaseModel):
    """Fleet-level statistics."""

    total_devices: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)


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


@router.get("", response_model=list[DeviceSummary])
async def list_devices(
    device_type: str | None = Query(None, description="Filter by device type"),
    status: str | None = Query(None, description="Filter by device status"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[DeviceSummary]:
    """Return an aggregated list of devices known to the cloud.

    Data is read from the locally synced ``devices`` table which is populated
    by periodic sync from edge gateways or direct registration.
    """
    query = "SELECT id, name, device_type, status, last_seen_at FROM devices WHERE 1=1"
    params: dict = {}

    if device_type:
        query += " AND device_type = :device_type"
        params["device_type"] = device_type
    if status:
        query += " AND status = :status"
        params["status"] = status

    query += " ORDER BY last_seen_at DESC NULLS LAST LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset

    try:
        result = await session.execute(text(query), params)
        rows = result.fetchall()
    except Exception:
        # Table may not exist yet — return empty list gracefully
        logger.warning("devices table not available, returning empty list")
        return []

    return [
        DeviceSummary(
            device_id=str(row[0]),
            name=row[1],
            device_type=row[2],
            status=row[3],
            last_seen_at=row[4].isoformat() if row[4] else None,
        )
        for row in rows
    ]


@router.get("/stats", response_model=DeviceStatsResponse)
async def device_stats(
    session: AsyncSession = Depends(get_session),
) -> DeviceStatsResponse:
    """Return fleet-level device statistics (count by type, count by status)."""
    stats = DeviceStatsResponse()

    try:
        # Total count
        total_result = await session.execute(text("SELECT COUNT(*) FROM devices"))
        stats.total_devices = total_result.scalar() or 0

        # Count by type
        type_result = await session.execute(
            text("SELECT device_type, COUNT(*) FROM devices GROUP BY device_type")
        )
        stats.by_type = {row[0]: row[1] for row in type_result.fetchall()}

        # Count by status
        status_result = await session.execute(
            text("SELECT status, COUNT(*) FROM devices GROUP BY status")
        )
        stats.by_status = {row[0]: row[1] for row in status_result.fetchall()}

    except Exception:
        logger.warning("devices table not available, returning zeroed stats")

    return stats
