"""Middleware / API Gateway configuration.

Extends the shared BaseServiceSettings with gateway-specific settings
for rate limiting, upstream service URLs, and HTTP port.
"""

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """Configuration for the Middleware API Gateway."""

    service_name: str = "middleware"
    http_port: int = 8000

    # Rate limiting
    rate_limit_requests: int = 100  # per window per client
    rate_limit_window_seconds: int = 60

    # Upstream service URLs
    upstream_device_manager: str = "http://device-manager:8002"
    upstream_data_ingestion: str = "http://data-ingestion:8001"
    upstream_ml_inference: str = "http://ml-inference:8004"
    upstream_scheduler: str = "http://scheduler:8005"

    # Refresh token settings
    jwt_refresh_expiration_minutes: int = 10080  # 7 days


def get_settings() -> Settings:
    """Factory that returns a cached Settings instance."""
    return Settings()
