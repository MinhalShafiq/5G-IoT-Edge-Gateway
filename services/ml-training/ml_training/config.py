"""ML Training service configuration."""

import torch

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """ML Training service settings.

    Extends the shared base with training-specific configuration for model
    output paths, registry integration, and hyperparameter defaults.
    """

    service_name: str = "ml-training"
    http_port: int = 8006
    model_output_dir: str = "/models"
    model_registry_url: str = "http://cloud-api:8080/api/v1/models"
    training_batch_size: int = 64
    training_epochs: int = 50
    learning_rate: float = 0.001
    device: str = "cuda" if torch.cuda.is_available() else "cpu"


def get_settings() -> Settings:
    """Return a cached settings instance."""
    return Settings()
