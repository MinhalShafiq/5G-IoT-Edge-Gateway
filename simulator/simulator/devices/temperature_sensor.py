"""Simulated temperature (and humidity) sensor device."""

import random

from simulator.devices.base_device import BaseDevice
from simulator.generators.anomaly_data import drift_anomaly, noise_burst, stuck_sensor
from simulator.generators.normal_data import gaussian_noise, sinusoidal


class TemperatureSensor(BaseDevice):
    """Simulates a temperature sensor with a co-located humidity reading.

    Normal behaviour follows a sinusoidal daily pattern (18-28 deg C)
    with additive Gaussian noise.  Three anomaly modes are supported:
    spike, drift, and stuck.
    """

    # Normal-range constants
    _TEMP_MIN: float = 18.0
    _TEMP_MAX: float = 28.0
    _TEMP_NOISE_STD: float = 0.5
    _HUMIDITY_MIN: float = 40.0
    _HUMIDITY_MAX: float = 60.0
    _HUMIDITY_NOISE_STD: float = 1.0

    # Anomaly constants
    _SPIKE_LOW: float = 80.0
    _SPIKE_HIGH: float = 120.0
    _DRIFT_INCREASE: float = 20.0
    _DRIFT_RATE: float = 0.5

    def __init__(
        self,
        device_id: str,
        sampling_interval: float = 5.0,
    ) -> None:
        super().__init__(
            device_id=device_id,
            device_type="temperature_sensor",
            sampling_interval=sampling_interval,
        )
        self._time_offset: float = 0.0
        self._last_temperature: float | None = None
        self._drift_steps: int = 0

    # ----- helpers -----

    def _normal_temperature(self) -> float:
        """Compute a normal temperature from the sinusoidal daily cycle."""
        amplitude = (self._TEMP_MAX - self._TEMP_MIN) / 2.0
        offset = (self._TEMP_MAX + self._TEMP_MIN) / 2.0
        # One full cycle every 24 "time units" (each reading advances by 1)
        frequency = 1.0 / 24.0
        base = sinusoidal(amplitude, frequency, offset, self._time_offset)
        return base + gaussian_noise(0.0, self._TEMP_NOISE_STD)

    def _normal_humidity(self) -> float:
        """Compute a normal humidity reading."""
        midpoint = (self._HUMIDITY_MAX + self._HUMIDITY_MIN) / 2.0
        return midpoint + gaussian_noise(0.0, self._HUMIDITY_NOISE_STD)

    # ----- public interface -----

    def generate_reading(self) -> dict:
        """Generate a normal temperature + humidity reading."""
        temperature = self._normal_temperature()
        humidity = self._normal_humidity()

        self._time_offset += 1.0
        self._last_temperature = temperature
        self._drift_steps = 0  # reset drift counter on normal readings

        return {
            "temperature_celsius": round(temperature, 2),
            "humidity_percent": round(max(0.0, min(100.0, humidity)), 2),
            "unit": "celsius",
            "anomaly": False,
        }

    def generate_anomaly(self) -> dict:
        """Generate an anomalous temperature reading.

        Randomly selects one of three anomaly types:
        - **spike**: temperature jumps to 80-120 deg C.
        - **drift**: temperature gradually increases by up to 20 deg C.
        - **stuck**: sensor reports the same value as the previous reading.
        """
        anomaly_type = random.choice(["spike", "drift", "stuck"])

        match anomaly_type:
            case "spike":
                temperature = random.uniform(self._SPIKE_LOW, self._SPIKE_HIGH)
            case "drift":
                base = self._last_temperature if self._last_temperature is not None else 23.0
                self._drift_steps += 1
                temperature = drift_anomaly(base, self._DRIFT_RATE, self._drift_steps)
                # Cap at the configured maximum drift increase above the base
                temperature = min(temperature, base + self._DRIFT_INCREASE)
            case "stuck":
                temperature = stuck_sensor(
                    self._last_temperature if self._last_temperature is not None else 23.0,
                )
            case _:
                temperature = noise_burst(self._normal_temperature(), noise_multiplier=15.0)

        self._time_offset += 1.0
        self._last_temperature = temperature

        humidity = self._normal_humidity()

        return {
            "temperature_celsius": round(temperature, 2),
            "humidity_percent": round(max(0.0, min(100.0, humidity)), 2),
            "unit": "celsius",
            "anomaly": True,
            "anomaly_type": anomaly_type,
        }
