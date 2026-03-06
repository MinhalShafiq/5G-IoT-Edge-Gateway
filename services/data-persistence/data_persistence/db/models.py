"""SQLAlchemy ORM models for the data persistence service."""

from uuid import uuid4

from sqlalchemy import Column, DateTime, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from shared.database.postgres import Base


class TelemetryRecord(Base):
    """Persisted telemetry reading in PostgreSQL.

    Each row represents a single sensor reading ingested from the
    raw_telemetry Redis Stream and batch-written to the database.
    """

    __tablename__ = "telemetry_readings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    device_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    device_type = Column(String(50), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    metadata_ = Column("metadata", JSON, default={})
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<TelemetryRecord(id={self.id}, device_id={self.device_id}, "
            f"device_type={self.device_type!r}, timestamp={self.timestamp})>"
        )
