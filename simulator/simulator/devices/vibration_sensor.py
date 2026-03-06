"""Simulated vibration / acceleration sensor device."""

import random

from simulator.devices.base_device import BaseDevice
from simulator.generators.anomaly_data import noise_burst, point_anomaly
from simulator.generators.normal_data import gaussian_noise


class VibrationSensor(BaseDevice):
    """Simulates a vibration sensor reporting RMS velocity and peak frequency.

    Normal behaviour produces RMS vibration in the range 0.5-2.0 mm/s and
    dominant frequency between 50-200 Hz.  Two anomaly modes are supported:
    bearing failure (high-amplitude spike) and erratic high-frequency burst.
    """

    # Normal-range constants
    _RMS_MIN: float = 0.5
    _RMS_MAX: float = 2.0
    _RMS_NOISE_STD: float = 0.1
    _FREQ_MIN: float = 50.0
    _FREQ_MAX: float = 200.0
    _FREQ_NOISE_STD: float = 5.0

    # Anomaly constants
    _BEARING_FAILURE_LOW: float = 10.0
    _BEARING_FAILURE_HIGH: float = 50.0
    _ERRATIC_FREQ_LOW: float = 800.0
    _ERRATIC_FREQ_HIGH: float = 2000.0

    def __init__(
        self,
        device_id: str,
        sampling_interval: float = 5.0,
    ) -> None:
        super().__init__(
            device_id=device_id,
            device_type="vibration_sensor",
            sampling_interval=sampling_interval,
        )

    # ----- helpers -----

    def _normal_rms(self) -> float:
        midpoint = (self._RMS_MAX + self._RMS_MIN) / 2.0
        return max(0.0, midpoint + gaussian_noise(0.0, self._RMS_NOISE_STD))

    def _normal_frequency(self) -> float:
        midpoint = (self._FREQ_MAX + self._FREQ_MIN) / 2.0
        return max(0.0, midpoint + gaussian_noise(0.0, self._FREQ_NOISE_STD))

    # ----- public interface -----

    def generate_reading(self) -> dict:
        """Generate a normal vibration reading."""
        rms_velocity = self._normal_rms()
        peak_frequency = self._normal_frequency()

        return {
            "rms_velocity_mm_s": round(rms_velocity, 3),
            "peak_frequency_hz": round(peak_frequency, 1),
            "unit": "mm/s",
            "anomaly": False,
        }

    def generate_anomaly(self) -> dict:
        """Generate an anomalous vibration reading.

        Randomly selects one of two anomaly types:
        - **bearing_failure**: RMS velocity spikes to 10-50 mm/s.
        - **erratic_burst**: high-frequency noise burst with amplified RMS.
        """
        anomaly_type = random.choice(["bearing_failure", "erratic_burst"])

        match anomaly_type:
            case "bearing_failure":
                rms_velocity = random.uniform(
                    self._BEARING_FAILURE_LOW,
                    self._BEARING_FAILURE_HIGH,
                )
                peak_frequency = self._normal_frequency()
            case "erratic_burst":
                rms_velocity = abs(point_anomaly(self._normal_rms(), multiplier_range=(5, 15)))
                peak_frequency = random.uniform(
                    self._ERRATIC_FREQ_LOW,
                    self._ERRATIC_FREQ_HIGH,
                )
            case _:
                rms_velocity = noise_burst(self._normal_rms(), noise_multiplier=30.0)
                peak_frequency = self._normal_frequency()

        return {
            "rms_velocity_mm_s": round(max(0.0, rms_velocity), 3),
            "peak_frequency_hz": round(max(0.0, peak_frequency), 1),
            "unit": "mm/s",
            "anomaly": True,
            "anomaly_type": anomaly_type,
        }
