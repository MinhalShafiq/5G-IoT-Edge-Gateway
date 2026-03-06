"""Simulated pressure sensor device with co-located flow-rate reading."""

import random

from simulator.devices.base_device import BaseDevice
from simulator.generators.anomaly_data import noise_burst, point_anomaly
from simulator.generators.normal_data import brownian_motion, gaussian_noise


class PressureSensor(BaseDevice):
    """Simulates a pressure sensor reporting barometric pressure and flow rate.

    Normal behaviour centres around 1013.25 hPa with slow Brownian drift
    and Gaussian noise.  Flow rate is sampled uniformly in 10-50 L/min.
    Two anomaly modes are supported: sudden pressure drop and overpressure.
    """

    # Normal-range constants
    _PRESSURE_BASELINE: float = 1013.25
    _PRESSURE_RANGE: float = 5.0  # +/- hPa around baseline
    _PRESSURE_NOISE_STD: float = 0.3
    _DRIFT_STEP_STD: float = 0.05
    _FLOW_RATE_MIN: float = 10.0
    _FLOW_RATE_MAX: float = 50.0
    _FLOW_NOISE_STD: float = 1.0

    # Anomaly constants
    _PRESSURE_DROP: float = -200.0
    _OVERPRESSURE: float = 500.0

    def __init__(
        self,
        device_id: str,
        sampling_interval: float = 5.0,
    ) -> None:
        super().__init__(
            device_id=device_id,
            device_type="pressure_sensor",
            sampling_interval=sampling_interval,
        )
        self._current_pressure: float = self._PRESSURE_BASELINE
        self._last_flow_rate: float = (self._FLOW_RATE_MIN + self._FLOW_RATE_MAX) / 2.0

    # ----- helpers -----

    def _normal_pressure(self) -> float:
        """Advance the Brownian drift and add noise, clamped to baseline +/- range."""
        self._current_pressure = brownian_motion(
            self._current_pressure,
            step_std=self._DRIFT_STEP_STD,
        )
        # Clamp to a reasonable band around baseline
        lower = self._PRESSURE_BASELINE - self._PRESSURE_RANGE
        upper = self._PRESSURE_BASELINE + self._PRESSURE_RANGE
        self._current_pressure = max(lower, min(upper, self._current_pressure))
        return self._current_pressure + gaussian_noise(0.0, self._PRESSURE_NOISE_STD)

    def _normal_flow_rate(self) -> float:
        midpoint = (self._FLOW_RATE_MAX + self._FLOW_RATE_MIN) / 2.0
        flow = midpoint + gaussian_noise(0.0, self._FLOW_NOISE_STD)
        return max(self._FLOW_RATE_MIN, min(self._FLOW_RATE_MAX, flow))

    # ----- public interface -----

    def generate_reading(self) -> dict:
        """Generate a normal pressure + flow-rate reading."""
        pressure = self._normal_pressure()
        flow_rate = self._normal_flow_rate()
        self._last_flow_rate = flow_rate

        return {
            "pressure_hpa": round(pressure, 2),
            "flow_rate_lpm": round(flow_rate, 2),
            "unit": "hPa",
            "anomaly": False,
        }

    def generate_anomaly(self) -> dict:
        """Generate an anomalous pressure reading.

        Randomly selects one of two anomaly types:
        - **pressure_drop**: sudden drop of ~200 hPa (e.g. seal failure).
        - **overpressure**: sudden spike of ~500 hPa (e.g. blockage).
        """
        anomaly_type = random.choice(["pressure_drop", "overpressure"])

        match anomaly_type:
            case "pressure_drop":
                pressure = self._normal_pressure() + self._PRESSURE_DROP
            case "overpressure":
                pressure = self._normal_pressure() + self._OVERPRESSURE
            case _:
                pressure = noise_burst(self._normal_pressure(), noise_multiplier=100.0)

        flow_rate = self._normal_flow_rate()

        return {
            "pressure_hpa": round(pressure, 2),
            "flow_rate_lpm": round(flow_rate, 2),
            "unit": "hPa",
            "anomaly": True,
            "anomaly_type": anomaly_type,
        }
