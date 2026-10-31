"""Stream consumer for real-time anomaly detection.

Reads telemetry from the raw_telemetry Redis Stream, runs ONNX inference,
and publishes alerts to the alerts stream when anomalies are detected.
"""

import asyncio
import time

import numpy as np

from shared.observability.logging_config import get_logger
from shared.observability.metrics import (
    ANOMALIES_DETECTED,
    STREAM_MESSAGES_CONSUMED,
    INFERENCE_LATENCY,
    INFERENCE_BATCH_SIZE,
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
    runs batched ONNX inference, and publishes alerts for anomalous readings.
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

        Runs until stop() is called. Each iteration reads up to ``batch_size``
        entries and scores them with a single ONNX Runtime call.
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
                    block_ms=1000,
                )

                if entries:
                    await self._process_batch(entries)

            except asyncio.CancelledError:
                logger.info("anomaly_detector_cancelled")
                break
            except Exception as e:
                logger.error("anomaly_detector_loop_error", error=str(e))
                await asyncio.sleep(1)

    async def _process_batch(self, entries: list[tuple[str, dict]]) -> None:
        """Extract features, score the batch, then publish alerts and ack in one round trip."""
        entry_ids = [entry_id for entry_id, _ in entries]
        readings: list[TelemetryReading] = []
        rows: list[list[float]] = []

        for entry_id, data in entries:
            try:
                reading = TelemetryReading.from_stream_dict(data)
                rows.append(self._extractor.extract_row(reading))
                readings.append(reading)
            except Exception as e:
                # Malformed entries are acked with the rest of the batch;
                # in production, consider a dead-letter stream instead.
                logger.error("invalid_stream_entry", entry_id=entry_id, error=str(e))

        pipe = self._redis.redis.pipeline(transaction=False)

        if readings:
            start = time.perf_counter()
            scores = self._engine.score(np.asarray(rows, dtype=np.float32))
            INFERENCE_LATENCY.labels(
                model_name=self._engine.model_name or "default"
            ).observe(time.perf_counter() - start)
            INFERENCE_BATCH_SIZE.observe(len(readings))

            threshold = self._settings.anomaly_threshold
            for reading, score in zip(readings, scores.tolist()):
                if score <= threshold:
                    continue

                severity = (
                    AlertSeverity.CRITICAL if score > 0.9 else AlertSeverity.WARNING
                )
                alert = Alert(
                    device_id=reading.device_id,
                    anomaly_score=score,
                    model_version=self._engine.model_version,
                    severity=severity,
                    details={"reading": reading.payload},
                )
                pipe.xadd(
                    StreamName.ALERTS,
                    alert.to_stream_dict(),
                    maxlen=self._settings.alerts_stream_max_len,
                    approximate=True,
                )
                ANOMALIES_DETECTED.labels(
                    device_type=reading.device_type, severity=alert.severity.value
                ).inc()
                logger.debug(
                    "anomaly_detected",
                    device_id=str(reading.device_id),
                    score=score,
                    severity=severity,
                )

        pipe.xack(StreamName.RAW_TELEMETRY, ConsumerGroup.ML_INFERENCE, *entry_ids)
        await pipe.execute()

        STREAM_MESSAGES_CONSUMED.labels(
            stream=StreamName.RAW_TELEMETRY,
            consumer_group=ConsumerGroup.ML_INFERENCE,
        ).inc(len(entry_ids))

    def stop(self) -> None:
        """Signal the consumer loop to stop."""
        self._running = False
        logger.info("anomaly_detector_stop_requested")
