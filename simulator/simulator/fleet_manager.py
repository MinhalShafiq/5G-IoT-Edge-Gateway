"""Fleet manager that orchestrates N simulated devices."""

from __future__ import annotations

import asyncio
import random
import time

import structlog

from simulator.devices.base_device import BaseDevice
from simulator.transport.mqtt_publisher import MqttPublisher

logger = structlog.get_logger(__name__)


class FleetManager:
    """Orchestrates a fleet of simulated IoT devices.

    Each publish cycle iterates over every device, decides (based on
    ``anomaly_rate``) whether the reading should be anomalous, and
    publishes the resulting telemetry message via the supplied
    :class:`MqttPublisher`.

    Args:
        devices: The list of simulated devices to manage.
        publisher: An :class:`MqttPublisher` instance (already connected or
            to be connected before calling :meth:`run`).
        anomaly_rate: Probability [0, 1] that any single reading is anomalous.
        interval: Seconds between consecutive publish cycles.
    """

    _STATS_LOG_INTERVAL: float = 30.0  # seconds between statistics logs

    def __init__(
        self,
        devices: list[BaseDevice],
        publisher: MqttPublisher,
        anomaly_rate: float = 0.05,
        interval: float = 5.0,
    ) -> None:
        self._devices = devices
        self._publisher = publisher
        self._anomaly_rate = anomaly_rate
        self._interval = interval

        self._running: bool = False
        self._total_published: int = 0
        self._total_anomalies: int = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _publish_device(self, device: BaseDevice) -> None:
        """Generate and publish a single device reading."""
        is_anomaly = random.random() < self._anomaly_rate
        telemetry = device.get_telemetry(anomaly=is_anomaly)

        # Offload the blocking MQTT publish to a thread so we don't
        # block the async event loop.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._publisher.publish,
            device.device_id,
            telemetry,
        )

        self._total_published += 1
        if is_anomaly:
            self._total_anomalies += 1

    async def _publish_cycle(self) -> None:
        """Run one full publish cycle for all devices concurrently."""
        tasks = [self._publish_device(device) for device in self._devices]
        await asyncio.gather(*tasks)

    async def _log_statistics(self) -> None:
        """Periodically log aggregate publishing statistics."""
        while self._running:
            await asyncio.sleep(self._STATS_LOG_INTERVAL)
            if self._running:
                logger.info(
                    "fleet_statistics",
                    total_published=self._total_published,
                    total_anomalies=self._total_anomalies,
                    anomaly_percent=(
                        round(self._total_anomalies / self._total_published * 100, 2)
                        if self._total_published > 0
                        else 0.0
                    ),
                    num_devices=len(self._devices),
                )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the main publish loop.

        This coroutine runs indefinitely until :meth:`stop` is called or
        the task is cancelled.
        """
        self._running = True
        logger.info(
            "fleet_starting",
            num_devices=len(self._devices),
            interval=self._interval,
            anomaly_rate=self._anomaly_rate,
        )

        # Launch the statistics logger as a background task
        stats_task = asyncio.create_task(self._log_statistics())

        try:
            while self._running:
                cycle_start = time.monotonic()
                await self._publish_cycle()
                elapsed = time.monotonic() - cycle_start

                # Sleep for the remainder of the interval (if any)
                sleep_time = max(0.0, self._interval - elapsed)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
        except asyncio.CancelledError:
            logger.info("fleet_cancelled")
        finally:
            self._running = False
            stats_task.cancel()
            try:
                await stats_task
            except asyncio.CancelledError:
                pass

        logger.info(
            "fleet_stopped",
            total_published=self._total_published,
            total_anomalies=self._total_anomalies,
        )

    def stop(self) -> None:
        """Signal the fleet to stop after the current cycle completes."""
        logger.info("fleet_stop_requested")
        self._running = False
