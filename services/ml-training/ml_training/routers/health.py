"""Health check router for the ML Training service."""

from shared.utils.health import create_health_router

router = create_health_router("ml-training")
