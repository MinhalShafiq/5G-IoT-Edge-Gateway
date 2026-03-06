"""Statistical data generators for producing realistic normal sensor readings."""

import math
import random


def sinusoidal(amplitude: float, frequency: float, offset: float, time: float) -> float:
    """Generate a sinusoidal value.

    Args:
        amplitude: Peak deviation from the offset.
        frequency: Oscillation frequency (cycles per unit time).
        offset: Centre-line / DC offset.
        time: Current time value.

    Returns:
        The sinusoidal value at the given time.
    """
    return offset + amplitude * math.sin(2.0 * math.pi * frequency * time)


def gaussian_noise(mean: float = 0.0, std: float = 1.0) -> float:
    """Generate a single sample of Gaussian (normal) noise.

    Args:
        mean: Mean of the distribution.
        std: Standard deviation of the distribution.

    Returns:
        A random float drawn from N(mean, std^2).
    """
    return random.gauss(mean, std)


def brownian_motion(current: float, step_std: float = 0.1) -> float:
    """Advance a Brownian-motion process by one step.

    Args:
        current: The current value of the process.
        step_std: Standard deviation of each incremental step.

    Returns:
        The next value after one random step.
    """
    return current + random.gauss(0.0, step_std)


def seasonal_pattern(
    time: float,
    daily_amp: float = 5.0,
    yearly_amp: float = 15.0,
) -> float:
    """Combine a daily and a yearly sinusoidal cycle.

    This is useful for modelling environmental quantities such as temperature
    that exhibit both diurnal and annual variation.

    Args:
        time: Current time in *hours*.
        daily_amp: Amplitude of the 24-hour cycle.
        yearly_amp: Amplitude of the 8760-hour (365-day) cycle.

    Returns:
        The combined seasonal offset (centred around 0).
    """
    daily_component = daily_amp * math.sin(2.0 * math.pi * time / 24.0)
    yearly_component = yearly_amp * math.sin(2.0 * math.pi * time / 8760.0)
    return daily_component + yearly_component
