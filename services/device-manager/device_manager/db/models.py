"""SQLAlchemy ORM models for the Device Manager service."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from shared.database.postgres import Base


class DeviceStatusEnum(str, enum.Enum):
    """Device lifecycle status."""

    REGISTERED = "registered"
    PROVISIONED = "provisioned"
    ACTIVE = "active"
    INACTIVE = "inactive"
    DECOMMISSIONED = "decommissioned"


class ProvisioningStatusEnum(str, enum.Enum):
    """Provisioning request status."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Device(Base):
    """IoT device registered in the gateway."""

    __tablename__ = "devices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, index=True)
    device_type = Column(String(100), nullable=False, index=True)
    status = Column(
        Enum(DeviceStatusEnum, name="device_status"),
        nullable=False,
        default=DeviceStatusEnum.REGISTERED,
        index=True,
    )
    firmware_version = Column(String(50), nullable=False, default="1.0.0")
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Relationships
    provisioning_requests = relationship(
        "ProvisioningRequest", back_populates="device", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_devices_type_status", "device_type", "status"),
    )

    def __repr__(self) -> str:
        return f"<Device(id={self.id}, name={self.name!r}, type={self.device_type})>"


class DeviceGroup(Base):
    """Logical grouping of devices for management and rollouts."""

    __tablename__ = "device_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    config = Column(JSON, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<DeviceGroup(id={self.id}, name={self.name!r})>"


class FirmwareVersion(Base):
    """Firmware image metadata for OTA updates."""

    __tablename__ = "firmware_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version = Column(String(50), nullable=False, index=True)
    device_type = Column(String(100), nullable=False, index=True)
    checksum = Column(String(128), nullable=False)
    file_url = Column(Text, nullable=False)
    release_notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_firmware_type_version", "device_type", "version", unique=True),
    )

    def __repr__(self) -> str:
        return (
            f"<FirmwareVersion(id={self.id}, version={self.version!r}, "
            f"device_type={self.device_type!r})>"
        )


class ProvisioningRequest(Base):
    """Record of a device provisioning request and its approval workflow."""

    __tablename__ = "provisioning_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(
        Enum(ProvisioningStatusEnum, name="provisioning_status"),
        nullable=False,
        default=ProvisioningStatusEnum.PENDING,
    )
    requested_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by = Column(String(255), nullable=True)

    # Relationships
    device = relationship("Device", back_populates="provisioning_requests")

    def __repr__(self) -> str:
        return (
            f"<ProvisioningRequest(id={self.id}, device_id={self.device_id}, "
            f"status={self.status})>"
        )
