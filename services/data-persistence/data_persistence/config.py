"""Configuration for the Data Persistence Worker."""

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """Data Persistence Worker settings.

    Extends the shared base settings with worker-specific configuration
    for batch writing telemetry data from Redis Streams to PostgreSQL.
    """

    service_name: str = "data-persistence"

    # Batch settings
    batch_size: int = 100  # How many readings to accumulate before flushing
    flush_interval_seconds: float = 5.0  # Max seconds before flushing even if batch isn't full

    # Consumer identity
    consumer_name: str = "persistence-worker-1"


settings = Settings()
