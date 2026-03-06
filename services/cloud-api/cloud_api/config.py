"""Cloud API Gateway configuration."""

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """Cloud API service settings.

    Extends the shared base with cloud-specific configuration for HTTP/gRPC
    ports, model storage, and edge gateway discovery.
    """

    service_name: str = "cloud-api"
    http_port: int = 8080
    grpc_port: int = 50051
    model_store_path: str = "/models"
    edge_gateway_urls: list[str] = []


def get_settings() -> Settings:
    """Return a cached settings instance."""
    return Settings()
