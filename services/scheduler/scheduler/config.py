"""Scheduler service configuration."""

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """Scheduler service settings.

    Extends the shared base with scheduler-specific configuration for edge/cloud
    placement decisions, latency SLA targets, and scheduling intervals.
    """

    service_name: str = "scheduler"
    http_port: int = 8005
    grpc_port: int = 50053
    edge_latency_sla_ms: float = 100.0
    cloud_endpoint: str = "http://cloud-api:8080"
    scheduling_interval_seconds: float = 1.0
    resource_stale_threshold_seconds: float = 30.0


def get_settings() -> Settings:
    """Return a cached settings instance."""
    return Settings()
