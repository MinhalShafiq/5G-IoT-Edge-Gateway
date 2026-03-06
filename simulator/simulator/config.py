"""Configuration for the IoT Device Simulator, loaded from environment variables."""

from pydantic_settings import BaseSettings


class SimulatorSettings(BaseSettings):
    """Settings for the IoT device simulator.

    All values can be overridden via environment variables, e.g.
    MQTT_BROKER_HOST=10.0.0.5 NUM_DEVICES=50 python -m simulator.main
    """

    # Transport: "mqtt", "rest", or "coap"
    transport: str = "mqtt"

    # MQTT settings
    mqtt_broker_host: str = "mosquitto"
    mqtt_broker_port: int = 1883

    # REST settings (used when transport="rest")
    rest_endpoint_host: str = "localhost"
    rest_endpoint_port: int = 8001

    # CoAP settings (used when transport="coap")
    coap_host: str = "localhost"
    coap_port: int = 5683

    num_devices: int = 10
    publish_interval_seconds: float = 5.0
    anomaly_rate: float = 0.05  # 5% chance of anomaly per reading
    device_types: list[str] = [
        "temperature_sensor",
        "vibration_sensor",
        "pressure_sensor",
    ]

    model_config = {
        "env_prefix": "",
        "case_sensitive": False,
    }
