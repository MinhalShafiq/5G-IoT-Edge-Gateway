"""ML Inference service configuration."""

import socket

from pydantic import Field

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """ML Inference service settings.

    Extends the shared base configuration with inference-specific fields.
    All values can be overridden via environment variables.
    """

    service_name: str = "ml-inference"
    http_port: int = 8004
    model_dir: str = "/app/model_store"
    # Scores are calibrated so 0.5 == the model's tuned error threshold.
    anomaly_threshold: float = 0.5
    consumer_group: str = "ml_inference"
    # Unique per container so replicas can share the consumer group.
    consumer_name: str = Field(default_factory=socket.gethostname)
    batch_size: int = 500
    alerts_stream_max_len: int = 100_000
    model_registry_url: str = "http://cloud-api:8080/api/v1/models"
    feature_window_size: int = 10


def get_settings() -> Settings:
    """Factory that returns a cached Settings instance."""
    return Settings()
