"""Batch writer service for efficient bulk inserts into PostgreSQL."""

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from data_persistence.db.models import TelemetryRecord
from shared.observability.logging_config import get_logger

logger = get_logger(__name__)


class BatchWriter:
    """Accumulates TelemetryRecord instances and flushes them in bulk.

    Uses SQLAlchemy's insert().values() for efficient multi-row inserts
    rather than adding objects one-by-one to the session.
    """

    def __init__(self) -> None:
        self._buffer: list[dict] = []

    @property
    def size(self) -> int:
        """Number of records currently buffered."""
        return len(self._buffer)

    def accumulate(self, record: TelemetryRecord) -> None:
        """Add a record to the internal buffer for later bulk insert.

        Args:
            record: A TelemetryRecord ORM instance to be persisted.
        """
        self._buffer.append(
            {
                "id": record.id,
                "device_id": record.device_id,
                "device_type": record.device_type,
                "timestamp": record.timestamp,
                "payload": record.payload,
                "metadata_": record.metadata_,
            }
        )

    async def flush(self, session: AsyncSession) -> int:
        """Bulk insert all buffered records into PostgreSQL.

        Args:
            session: An active async SQLAlchemy session.

        Returns:
            The number of records inserted.
        """
        if not self._buffer:
            return 0

        count = len(self._buffer)
        # Use Core insert for efficient bulk operations.
        # Map the dict key 'metadata_' back to the column name 'metadata'.
        values = []
        for row in self._buffer:
            values.append(
                {
                    "id": row["id"],
                    "device_id": row["device_id"],
                    "device_type": row["device_type"],
                    "timestamp": row["timestamp"],
                    "payload": row["payload"],
                    "metadata": row["metadata_"],
                }
            )

        stmt = insert(TelemetryRecord.__table__).values(values)
        await session.execute(stmt)
        await session.commit()

        logger.info("batch_flushed", record_count=count)
        self._buffer.clear()
        return count
