"""Integration test: stream entries are written to PostgreSQL."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from shared.models.telemetry import TelemetryReading
from data_persistence.db.models import TelemetryRecord
from data_persistence.services.batch_writer import BatchWriter
from data_persistence.workers.stream_to_postgres import _telemetry_to_record


@pytest.mark.asyncio
async def test_batch_writer_inserts_records(db_session):
    """Regression: Core bulk insert used to send id=NULL and fail every flush."""
    writer = BatchWriter()
    device_ids = [uuid4() for _ in range(3)]
    for device_id in device_ids:
        reading = TelemetryReading(
            device_id=device_id,
            device_type="pressure_sensor",
            payload={"pressure_hpa": 1013.2, "flow_rate_lpm": 30.1},
        )
        writer.accumulate(_telemetry_to_record(reading))

    assert await writer.flush(db_session) == 3
    count = await db_session.scalar(
        select(func.count())
        .select_from(TelemetryRecord)
        .where(TelemetryRecord.device_id.in_(device_ids))
    )
    assert count == 3
