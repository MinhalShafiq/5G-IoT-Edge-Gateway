"""Pre-configured factory-floor scenario.

Creates a mixed fleet of 50 devices (20 temperature, 15 vibration,
15 pressure) with a 5-second publish interval and 5% anomaly rate.
"""

import uuid

from simulator.devices.base_device import BaseDevice
from simulator.devices.pressure_sensor import PressureSensor
from simulator.devices.temperature_sensor import TemperatureSensor
from simulator.devices.vibration_sensor import VibrationSensor

# Scenario constants
NUM_TEMPERATURE: int = 20
NUM_VIBRATION: int = 15
NUM_PRESSURE: int = 15
PUBLISH_INTERVAL: float = 5.0
ANOMALY_RATE: float = 0.05


def create_factory_floor_fleet() -> list[BaseDevice]:
    """Build and return the full list of devices for the factory-floor scenario.

    Returns:
        A list of 50 ``BaseDevice`` instances ready for use with
        :class:`~simulator.fleet_manager.FleetManager`.
    """
    devices: list[BaseDevice] = []

    for i in range(NUM_TEMPERATURE):
        devices.append(
            TemperatureSensor(
                device_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"temp-{i:03d}")),
                sampling_interval=PUBLISH_INTERVAL,
            )
        )

    for i in range(NUM_VIBRATION):
        devices.append(
            VibrationSensor(
                device_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"vib-{i:03d}")),
                sampling_interval=PUBLISH_INTERVAL,
            )
        )

    for i in range(NUM_PRESSURE):
        devices.append(
            PressureSensor(
                device_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"pres-{i:03d}")),
                sampling_interval=PUBLISH_INTERVAL,
            )
        )

    return devices
