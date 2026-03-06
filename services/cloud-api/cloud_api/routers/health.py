"""Health check router for the Cloud API Gateway."""

from shared.utils.health import create_health_router

router = create_health_router("cloud-api")
