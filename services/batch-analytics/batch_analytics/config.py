"""Batch Analytics service configuration."""

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """Batch Analytics service settings.

    Extends the shared base with Spark-specific configuration for the
    PySpark driver, executor memory, and Spark master URL.
    """

    service_name: str = "batch-analytics"
    http_port: int = 8007

    # Spark configuration
    spark_master: str = "local[*]"
    spark_app_name: str = "iot-batch-analytics"
    spark_driver_memory: str = "2g"
    spark_executor_memory: str = "2g"


def get_settings() -> Settings:
    """Return a cached settings instance."""
    return Settings()
