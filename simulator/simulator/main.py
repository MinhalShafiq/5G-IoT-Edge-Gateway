"""CLI entrypoint for the IoT Device Simulator.

Parses configuration from environment variables / defaults, creates the
device fleet, and runs the publish loop until interrupted.
"""

from __future__ import annotations

import asyncio
import random
import signal
import sys
import uuid

import structlog

from simulator.config import SimulatorSettings
from simulator.devices.base_device import BaseDevice
from simulator.devices.pressure_sensor import PressureSensor
from simulator.devices.temperature_sensor import TemperatureSensor
from simulator.devices.vibration_sensor import VibrationSensor
from simulator.fleet_manager import FleetManager
from simulator.transport.mqtt_publisher import MqttPublisher
from simulator.transport.rest_client import RestPublisher
from simulator.transport.coap_client import CoapPublisher

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(0),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Device factory
# ---------------------------------------------------------------------------

_DEVICE_FACTORY: dict[str, type[BaseDevice]] = {
    "temperature_sensor": TemperatureSensor,
    "vibration_sensor": VibrationSensor,
    "pressure_sensor": PressureSensor,
}


def _create_device_fleet(settings: SimulatorSettings) -> list[BaseDevice]:
    """Instantiate the configured number of devices, cycling through types."""
    devices: list[BaseDevice] = []
    type_count: dict[str, int] = {}

    for i in range(settings.num_devices):
        device_type = settings.device_types[i % len(settings.device_types)]
        count = type_count.get(device_type, 0)
        type_count[device_type] = count + 1

        cls = _DEVICE_FACTORY.get(device_type)
        if cls is None:
            logger.warning("unknown_device_type", device_type=device_type)
            continue

        human_name = f"{device_type[:4]}-{count:04d}"
        device_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, human_name))
        devices.append(
            cls(
                device_id=device_id,
                sampling_interval=settings.publish_interval_seconds,
            )
        )

    return devices


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse config, build fleet, and run the event loop."""
    settings = SimulatorSettings()

    logger.info(
        "simulator_starting",
        transport=settings.transport,
        num_devices=settings.num_devices,
        publish_interval=settings.publish_interval_seconds,
        anomaly_rate=settings.anomaly_rate,
        device_types=settings.device_types,
    )

    # Build device fleet
    devices = _create_device_fleet(settings)
    if not devices:
        logger.error("no_devices_created")
        sys.exit(1)

    logger.info("fleet_created", num_devices=len(devices))

    # Set up publisher based on transport selection
    match settings.transport.lower():
        case "mqtt":
            publisher = MqttPublisher()
            publisher.connect(settings.mqtt_broker_host, settings.mqtt_broker_port)
        case "rest":
            publisher = RestPublisher()
            publisher.connect(settings.rest_endpoint_host, settings.rest_endpoint_port)
        case "coap":
            publisher = CoapPublisher()
            publisher.connect(settings.coap_host, settings.coap_port)
        case _:
            logger.error("unknown_transport", transport=settings.transport)
            sys.exit(1)

    logger.info("publisher_connected", transport=settings.transport)

    # Build fleet manager
    fleet = FleetManager(
        devices=devices,
        publisher=publisher,
        anomaly_rate=settings.anomaly_rate,
        interval=settings.publish_interval_seconds,
    )

    # Run the asyncio event loop with graceful shutdown
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    fleet_task = loop.create_task(fleet.run())

    def _shutdown_handler() -> None:
        logger.info("shutdown_signal_received")
        fleet.stop()

    # Register signal handlers for graceful Ctrl+C
    try:
        loop.add_signal_handler(signal.SIGINT, _shutdown_handler)
        loop.add_signal_handler(signal.SIGTERM, _shutdown_handler)
    except NotImplementedError:
        # add_signal_handler is not available on Windows; fall back to
        # wrapping the run in a KeyboardInterrupt handler (see below).
        pass

    try:
        loop.run_until_complete(fleet_task)
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
        fleet.stop()
        loop.run_until_complete(fleet_task)
    finally:
        publisher.disconnect()
        loop.close()

    logger.info("simulator_exited")


if __name__ == "__main__":
    main()
