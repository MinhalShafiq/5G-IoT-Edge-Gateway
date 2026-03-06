"""Device Manager service configuration."""

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """Device Manager service settings.

    Extends BaseServiceSettings with HTTP port and any
    device-manager-specific configuration.
    """

    service_name: str = "device-manager"

    # HTTP
    http_port: int = 8002
