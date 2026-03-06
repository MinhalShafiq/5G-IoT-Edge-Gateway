"""Anomaly injection functions for generating synthetic faults in sensor data."""

import random


def point_anomaly(
    base_value: float,
    multiplier_range: tuple[float, float] = (3.0, 10.0),
) -> float:
    """Create a single-point outlier by scaling the base value.

    Args:
        base_value: The normal reading that will be amplified.
        multiplier_range: (min, max) multiplier applied to the base value.

    Returns:
        An anomalous value far outside the normal range.
    """
    multiplier = random.uniform(*multiplier_range)
    sign = random.choice([-1, 1])
    return base_value + sign * abs(base_value) * multiplier


def drift_anomaly(
    base_value: float,
    drift_rate: float,
    elapsed_steps: int,
) -> float:
    """Simulate gradual sensor drift over time.

    The drift grows linearly with the number of elapsed steps.

    Args:
        base_value: The normal reading before drift.
        drift_rate: How much the value shifts per step.
        elapsed_steps: Number of steps since the drift began.

    Returns:
        The drifted value.
    """
    return base_value + drift_rate * elapsed_steps


def stuck_sensor(last_value: float) -> float:
    """Simulate a frozen/stuck sensor that keeps reporting the same value.

    Args:
        last_value: The last value the sensor reported before freezing.

    Returns:
        Exactly ``last_value`` (unchanged).
    """
    return last_value


def noise_burst(
    base_value: float,
    noise_multiplier: float = 20.0,
) -> float:
    """Inject erratic high-amplitude noise into a reading.

    Args:
        base_value: The normal reading before the burst.
        noise_multiplier: Controls how large the random noise can be
            relative to the base value.

    Returns:
        A highly noisy value.
    """
    noise = random.uniform(-1.0, 1.0) * noise_multiplier
    return base_value + noise
