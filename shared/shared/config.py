"""Base configuration using Pydantic Settings.

All services inherit from BaseServiceSettings and extend with service-specific fields.
Configuration is loaded from environment variables with sensible defaults for local dev.
"""

from pydantic_settings import BaseSettings


class BaseServiceSettings(BaseSettings):
    """Base settings inherited by every microservice."""

    service_name: str = "unknown"
    environment: str = "development"
    log_level: str = "INFO"

    # Redis
    redis_url: str = "redis://redis:6379/0"
    redis_max_connections: int = 20

    # PostgreSQL
    postgres_dsn: str = (
        "postgresql+asyncpg://iot_gateway:changeme@postgres:5432/iot_gateway"
    )

    # Auth
    jwt_secret: str = "dev-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60

    # Observability
    prometheus_port: int = 9090
    enable_tracing: bool = False

    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}
