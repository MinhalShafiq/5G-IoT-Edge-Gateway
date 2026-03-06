"""ML Inference service configuration."""

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """ML Inference service settings.

    Extends the shared base configuration with inference-specific fields.
    All values can be overridden via environment variables.
    """

    service_name: str = "ml-inference"
    http_port: int = 8004
    model_dir: str = "/app/model_store"
    anomaly_threshold: float = 0.7
    consumer_group: str = "ml_inference"
    consumer_name: str = "inference-worker-1"
    batch_size: int = 32
    model_registry_url: str = "http://cloud-api:8080/api/v1/models"
    feature_window_size: int = 10


def get_settings() -> Settings:
    """Factory that returns a cached Settings instance."""
    return Settings()
