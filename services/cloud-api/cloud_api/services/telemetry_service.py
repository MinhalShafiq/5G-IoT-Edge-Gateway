"""Telemetry business logic.

Handles bulk storage and querying of telemetry readings in PostgreSQL.
The ORM table ``telemetry_readings`` mirrors the shared TelemetryReading
Pydantic model.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    String,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSON, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.postgres import Base
from shared.models.telemetry import TelemetryReading

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# ORM Table
# ---------------------------------------------------------------------------


class TelemetryRow(Base):
    """PostgreSQL table for persisted telemetry readings."""

    __tablename__ = "telemetry_readings"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    device_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    device_type = Column(String(100), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    payload = Column(JSON, nullable=False, default={})
    metadata_ = Column("metadata", JSON, nullable=True, default={})


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------


async def store_batch(
    readings: list[TelemetryReading],
    session: AsyncSession,
) -> int:
    """Bulk-insert telemetry readings and return the number accepted.

    Each reading is mapped to a ``TelemetryRow`` and added to the session
    in one go for efficiency.
    """
    if not readings:
        return 0

    rows = [
        TelemetryRow(
            id=uuid4(),
            device_id=reading.device_id,
            device_type=reading.device_type,
            timestamp=reading.timestamp,
            payload=reading.payload,
            metadata_=reading.metadata,
        )
        for reading in readings
    ]

    session.add_all(rows)
    await session.commit()

    logger.info("telemetry batch stored", count=len(rows))
    return len(rows)


async def query_telemetry(
    device_id: UUID | None,
    start_time: datetime | None,
    end_time: datetime | None,
    device_type: str | None,
    limit: int,
    offset: int,
    session: AsyncSession,
) -> tuple[list[dict], int]:
    """Query telemetry readings with optional filters.

    Returns a tuple of (results, total_count) for pagination support.
    """
    # Build count query
    count_q = select(func.count()).select_from(TelemetryRow)
    data_q = select(TelemetryRow)

    if device_id is not None:
        count_q = count_q.where(TelemetryRow.device_id == device_id)
        data_q = data_q.where(TelemetryRow.device_id == device_id)
    if device_type is not None:
        count_q = count_q.where(TelemetryRow.device_type == device_type)
        data_q = data_q.where(TelemetryRow.device_type == device_type)
    if start_time is not None:
        count_q = count_q.where(TelemetryRow.timestamp >= start_time)
        data_q = data_q.where(TelemetryRow.timestamp >= start_time)
    if end_time is not None:
        count_q = count_q.where(TelemetryRow.timestamp <= end_time)
        data_q = data_q.where(TelemetryRow.timestamp <= end_time)

    data_q = data_q.order_by(TelemetryRow.timestamp.desc()).limit(limit).offset(offset)

    total = (await session.execute(count_q)).scalar() or 0
    result = await session.execute(data_q)
    rows = result.scalars().all()

    readings = [
        {
            "id": str(row.id),
            "device_id": str(row.device_id),
            "device_type": row.device_type,
            "timestamp": row.timestamp.isoformat(),
            "payload": row.payload,
            "metadata": row.metadata_,
        }
        for row in rows
    ]

    return readings, total


async def get_stats(session: AsyncSession) -> dict:
    """Return aggregate statistics about stored telemetry.

    Keys: total_readings, readings_by_device_type, earliest_reading,
    latest_reading.
    """
    total_result = await session.execute(
        select(func.count()).select_from(TelemetryRow)
    )
    total = total_result.scalar() or 0

    type_result = await session.execute(
        select(TelemetryRow.device_type, func.count())
        .group_by(TelemetryRow.device_type)
    )
    by_type = {row[0]: row[1] for row in type_result.fetchall()}

    range_result = await session.execute(
        select(func.min(TelemetryRow.timestamp), func.max(TelemetryRow.timestamp))
    )
    time_range = range_result.first()
    earliest = time_range[0].isoformat() if time_range and time_range[0] else None
    latest = time_range[1].isoformat() if time_range and time_range[1] else None

    return {
        "total_readings": total,
        "readings_by_device_type": by_type,
        "earliest_reading": earliest,
        "latest_reading": latest,
    }
