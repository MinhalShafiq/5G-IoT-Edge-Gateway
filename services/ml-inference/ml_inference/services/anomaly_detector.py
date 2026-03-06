"""Stream consumer for real-time anomaly detection.

Reads telemetry from the raw_telemetry Redis Stream, runs ONNX inference,
and publishes alerts to the alerts stream when anomalies are detected.
"""

import asyncio
import math

import numpy as np

from shared.observability.logging_config import get_logger
from shared.observability.metrics import (
    ANOMALIES_DETECTED,
    STREAM_MESSAGES_CONSUMED,
    INFERENCE_LATENCY,
)
from shared.models.telemetry import TelemetryReading
from shared.models.alert import Alert, AlertSeverity
from shared.streams.constants import StreamName, ConsumerGroup
from shared.database.redis_client import RedisClient

from ml_inference.config import Settings
from ml_inference.services.inference_engine import InferenceEngine
from ml_inference.services.feature_extractor import FeatureExtractor

logger = get_logger(__name__)


class AnomalyDetector:
    """Background stream consumer that runs real-time anomaly detection.

    Reads batches of telemetry from Redis Streams, extracts features,
    runs ONNX inference, and publishes alerts for anomalous readings.
    """

    def __init__(
        self,
        redis_client: RedisClient,
        inference_engine: InferenceEngine,
        feature_extractor: FeatureExtractor,
        settings: Settings,
    ):
        self._redis = redis_client
        self._engine = inference_engine
        self._extractor = feature_extractor
        self._settings = settings
        self._running = True

    async def run(self) -> None:
        """Main loop: read from raw_telemetry stream, run inference, publish alerts.

        This method runs indefinitely until stop() is called. It processes
        messages in batches, extracts features per reading, runs inference,
        and publishes alerts to the alerts stream for any anomalous readings.
        """
        logger.info(
            "anomaly_detector_started",
            consumer_group=ConsumerGroup.ML_INFERENCE,
            consumer_name=self._settings.consumer_name,
            batch_size=self._settings.batch_size,
        )

        while self._running:
            try:
                entries = await self._redis.stream_read_group(
                    StreamName.RAW_TELEMETRY,
                    ConsumerGroup.ML_INFERENCE,
                    self._settings.consumer_name,
                    count=self._settings.batch_size,
                )

                if not entries:
                    continue

                for entry_id, data in entries:
                    await self._process_entry(entry_id, data)

            except asyncio.CancelledError:
                logger.info("anomaly_detector_cancelled")
                break
            except Exception as e:
                logger.error("anomaly_detector_loop_error", error=str(e))
                await asyncio.sleep(1)

    async def _process_entry(self, entry_id: str, data: dict) -> None:
        """Process a single stream entry: extract features, infer, and alert."""
        try:
            reading = TelemetryReading.from_stream_dict(data)
            features = self._extractor.extract(reading)

            import time

            start = time.perf_counter()
            score = self._engine.predict(features)
            latency = time.perf_counter() - start

            INFERENCE_LATENCY.labels(
                model_name=self._engine._model_name or "default"
            ).observe(latency)

            # IsolationForest decision scores: positive = normal, negative = anomaly
            raw = float(np.asarray(score).flat[0])
            # Sigmoid to map to [0, 1]: negative scores → high anomaly score
            anomaly_score = 1.0 / (1.0 + math.exp(40.0 * raw))
            anomaly_score = max(0.0, min(1.0, anomaly_score))

            if anomaly_score > self._settings.anomaly_threshold:
                severity = (
                    AlertSeverity.CRITICAL
                    if anomaly_score > 0.9
                    else AlertSeverity.WARNING
                )

                alert = Alert(
                    device_id=reading.device_id,
                    anomaly_score=anomaly_score,
                    model_version=self._engine._model_version,
                    severity=severity,
                    details={"reading": reading.payload},
                )

                await self._redis.stream_add(
                    StreamName.ALERTS, alert.to_stream_dict()
                )

                ANOMALIES_DETECTED.labels(
                    device_type=reading.device_type, severity=alert.severity
                ).inc()

                logger.info(
                    "anomaly_detected",
                    device_id=str(reading.device_id),
                    score=anomaly_score,
                    severity=severity,
                )

            await self._redis.stream_ack(
                StreamName.RAW_TELEMETRY, ConsumerGroup.ML_INFERENCE, entry_id
            )
            STREAM_MESSAGES_CONSUMED.labels(
                stream=StreamName.RAW_TELEMETRY,
                consumer_group=ConsumerGroup.ML_INFERENCE,
            ).inc()

        except Exception as e:
            logger.error("inference_error", entry_id=entry_id, error=str(e))
            # Acknowledge the message even on error to prevent infinite reprocessing.
            # In production, consider a dead-letter queue instead.
            try:
                await self._redis.stream_ack(
                    StreamName.RAW_TELEMETRY, ConsumerGroup.ML_INFERENCE, entry_id
                )
            except Exception:
                pass

    def stop(self) -> None:
        """Signal the consumer loop to stop."""
        self._running = False
        logger.info("anomaly_detector_stop_requested")
