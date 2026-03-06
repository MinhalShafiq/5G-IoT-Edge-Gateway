"""Data Ingestion service configuration."""

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """Data Ingestion service settings.

    Extends BaseServiceSettings with MQTT broker configuration,
    CoAP port, Redis Stream limits, and HTTP port.
    """

    service_name: str = "data-ingestion"

    # MQTT
    mqtt_broker_host: str = "mosquitto"
    mqtt_broker_port: int = 1883
    mqtt_topics: list[str] = ["devices/+/telemetry"]

    # CoAP
    coap_port: int = 5683

    # Redis Streams
    stream_max_len: int = 100_000

    # HTTP
    http_port: int = 8001
