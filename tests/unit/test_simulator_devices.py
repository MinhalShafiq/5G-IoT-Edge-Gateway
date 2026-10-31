"""Unit tests for simulated device behaviour the anomaly model depends on."""

import random
import statistics

from simulator.devices.pressure_sensor import PressureSensor


def _pressures(devices: list[PressureSensor]) -> list[float]:
    return [d.generate_reading()["pressure_hpa"] for d in devices]


def test_pressure_fleet_distribution_does_not_drift_with_age():
    """A fleet's pressure spread must look the same at start-up and hours later.

    The model is trained on short simulated histories; if long-running devices
    wandered outside the training distribution, normal readings would start
    raising alerts (this happened before devices started at a random point
    in their band).
    """
    random.seed(0)
    fleet = [PressureSensor(device_id=str(i)) for i in range(1000)]

    young = _pressures(fleet)
    for _ in range(1000):  # ~1.4 hours at one reading every 5 s
        _pressures(fleet)
    old = _pressures(fleet)

    assert abs(statistics.stdev(old) - statistics.stdev(young)) < 0.3
    assert abs(statistics.mean(old) - statistics.mean(young)) < 0.3
