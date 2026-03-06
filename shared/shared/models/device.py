"""Device domain models."""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class DeviceType(str, Enum):
    TEMPERATURE_SENSOR = "temperature_sensor"
    VIBRATION_SENSOR = "vibration_sensor"
    PRESSURE_SENSOR = "pressure_sensor"
    SMART_METER = "smart_meter"
    GPS_TRACKER = "gps_tracker"


class DeviceStatus(str, Enum):
    REGISTERED = "registered"
    PROVISIONED = "provisioned"
    ACTIVE = "active"
    INACTIVE = "inactive"
    DECOMMISSIONED = "decommissioned"


class DeviceCreate(BaseModel):
    """Schema for registering a new device."""

    name: str = Field(..., max_length=255)
    device_type: DeviceType
    firmware_version: str = "1.0.0"
    metadata: dict = Field(default_factory=dict)


class Device(BaseModel):
    """Full device representation."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    device_type: DeviceType
    status: DeviceStatus = DeviceStatus.REGISTERED
    firmware_version: str = "1.0.0"
    last_seen_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"from_attributes": True}
