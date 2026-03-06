"""Telemetry data models."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TelemetryReading(BaseModel):
    """A single telemetry reading from an IoT device."""

    device_id: UUID
    device_type: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    payload: dict = Field(
        ..., description="Sensor-specific data (e.g., temperature, humidity)"
    )
    metadata: dict = Field(
        default_factory=dict,
        description="Device metadata (firmware_version, signal_strength, etc.)",
    )

    def to_stream_dict(self) -> dict[str, str]:
        """Serialize to flat dict for Redis XADD."""
        return {
            "device_id": str(self.device_id),
            "device_type": self.device_type,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.model_dump_json(include={"payload"}),
            "metadata": self.model_dump_json(include={"metadata"}),
        }

    @classmethod
    def from_stream_dict(cls, data: dict[str, str]) -> "TelemetryReading":
        """Deserialize from Redis Stream entry."""
        import json

        return cls(
            device_id=data["device_id"],
            device_type=data["device_type"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            payload=json.loads(data["payload"])["payload"],
            metadata=json.loads(data["metadata"])["metadata"],
        )
