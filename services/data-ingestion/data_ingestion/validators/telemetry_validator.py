from datetime import datetime, timedelta
from shared.models.telemetry import TelemetryReading
from shared.observability.logging_config import get_logger

logger = get_logger(__name__)


class ValidationError(Exception):
    pass


def validate_telemetry(reading: TelemetryReading) -> TelemetryReading:
    """Validate a telemetry reading. Raises ValidationError on failure."""
    # Check timestamp is not too far in the future (>5 min) or past (>24h)
    now = datetime.utcnow()
    if reading.timestamp > now + timedelta(minutes=5):
        raise ValidationError(f"Timestamp too far in the future: {reading.timestamp}")
    if reading.timestamp < now - timedelta(hours=24):
        raise ValidationError(f"Timestamp too old: {reading.timestamp}")

    # Check payload is not empty
    if not reading.payload:
        raise ValidationError("Empty payload")

    # Check device_id is valid UUID format
    try:
        from uuid import UUID
        UUID(str(reading.device_id))
    except ValueError:
        raise ValidationError(f"Invalid device_id: {reading.device_id}")

    return reading
