"""Analytics endpoints.

Provides pre-aggregated anomaly summaries, device health scores, and a
flexible query interface for ad-hoc analytics against the telemetry and
alert data stored in PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.postgres import get_async_session

from cloud_api.config import Settings, get_settings
from cloud_api.services.analytics_service import (
    anomaly_summary,
    device_health_scores,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/analytics", tags=["analytics"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AnomalySummaryResponse(BaseModel):
    """Summary of anomalies over a specified period."""

    total_anomalies: int = 0
    by_device_type: dict[str, int] = Field(default_factory=dict)
    by_severity: dict[str, int] = Field(default_factory=dict)
    start_time: datetime | None = None
    end_time: datetime | None = None


class DeviceHealthEntry(BaseModel):
    """Health score for a single device."""

    device_id: str
    device_type: str = ""
    anomaly_count: int = 0
    total_readings: int = 0
    health_score: float = Field(1.0, ge=0.0, le=1.0)


class DeviceHealthResponse(BaseModel):
    """Collection of device health scores."""

    devices: list[DeviceHealthEntry] = Field(default_factory=list)


class AnalyticsQuery(BaseModel):
    """Flexible analytics query parameters."""

    metric: str = "telemetry_count"
    device_type: str | None = None
    device_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    group_by: str | None = None
    limit: int = 100


class AnalyticsQueryResponse(BaseModel):
    """Response for the flexible analytics query endpoint."""

    metric: str
    results: list[dict] = Field(default_factory=list)
    total: int = 0


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


@router.get("/anomaly-summary", response_model=AnomalySummaryResponse)
async def get_anomaly_summary(
    start_time: datetime | None = Query(
        None, description="Start of time window (ISO 8601)"
    ),
    end_time: datetime | None = Query(
        None, description="End of time window (ISO 8601)"
    ),
    session: AsyncSession = Depends(get_session),
) -> AnomalySummaryResponse:
    """Return a summary of anomalies detected within the given time period.

    Defaults to the last 24 hours if no time range is specified.
    """
    now = datetime.utcnow()
    if end_time is None:
        end_time = now
    if start_time is None:
        start_time = now - timedelta(hours=24)

    summary = await anomaly_summary(start_time, end_time, session)
    summary["start_time"] = start_time
    summary["end_time"] = end_time
    return AnomalySummaryResponse(**summary)


@router.get("/device-health", response_model=DeviceHealthResponse)
async def get_device_health(
    session: AsyncSession = Depends(get_session),
) -> DeviceHealthResponse:
    """Return health scores for every device, computed from the recent anomaly rate."""
    scores = await device_health_scores(session)
    return DeviceHealthResponse(devices=scores)


@router.post("/query", response_model=AnalyticsQueryResponse)
async def run_analytics_query(
    query: AnalyticsQuery,
    session: AsyncSession = Depends(get_session),
) -> AnalyticsQueryResponse:
    """Flexible analytics query endpoint.

    Accepts SQL-like filter parameters and returns aggregated results.
    Currently supports the following metrics:

    * ``telemetry_count`` -- count of telemetry readings grouped by the
      requested dimension.
    * ``anomaly_count`` -- count of anomaly alerts grouped by the requested
      dimension.
    """
    from sqlalchemy import text

    now = datetime.utcnow()
    start = query.start_time or (now - timedelta(days=7))
    end = query.end_time or now

    params: dict = {"start": start, "end": end, "limit": query.limit}

    if query.metric == "anomaly_count":
        table = "alerts"
        time_col = "timestamp"
    else:
        table = "telemetry_readings"
        time_col = "timestamp"

    group_col = query.group_by or "device_type"
    sql = (
        f"SELECT {group_col}, COUNT(*) as cnt "
        f"FROM {table} "
        f"WHERE {time_col} >= :start AND {time_col} <= :end"
    )

    if query.device_type:
        sql += " AND device_type = :device_type"
        params["device_type"] = query.device_type
    if query.device_id:
        sql += " AND device_id = :device_id"
        params["device_id"] = query.device_id

    sql += f" GROUP BY {group_col} ORDER BY cnt DESC LIMIT :limit"

    try:
        result = await session.execute(text(sql), params)
        rows = result.fetchall()
        results = [{group_col: row[0], "count": row[1]} for row in rows]
        total = sum(r["count"] for r in results)
    except Exception:
        logger.warning("analytics query failed, table may not exist", metric=query.metric)
        results = []
        total = 0

    return AnalyticsQueryResponse(
        metric=query.metric,
        results=results,
        total=total,
    )
