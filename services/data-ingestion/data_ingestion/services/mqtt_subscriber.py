"""Async MQTT subscriber that bridges MQTT messages to Redis Streams.

Connects to the configured MQTT broker, subscribes to device telemetry topics,
parses incoming JSON payloads into TelemetryReading models, and hands them off
to the ingestion service for processing and publishing.
"""

import asyncio
import json
from datetime import datetime
from uuid import UUID

import aiomqtt

from shared.database.redis_client import RedisClient
from shared.models.telemetry import TelemetryReading
from shared.observability.logging_config import get_logger

from data_ingestion.config import Settings
from data_ingestion.services import ingestion_service

logger = get_logger(__name__)

# Exponential backoff constants for reconnection
_BASE_RECONNECT_DELAY = 1.0  # seconds
_MAX_RECONNECT_DELAY = 60.0  # seconds
_BACKOFF_MULTIPLIER = 2.0


class MQTTSubscriber:
    """Long-lived async MQTT subscriber task.

    Connects to the broker, subscribes to configured topics, and processes
    each incoming message through the ingestion pipeline. Automatically
    reconnects with exponential backoff on connection failures.
    """

    def __init__(self, settings: Settings, redis_client: RedisClient) -> None:
        self._settings = settings
        self._redis_client = redis_client
        self._running = True
        self._reconnect_delay = _BASE_RECONNECT_DELAY

    def stop(self) -> None:
        """Signal the subscriber to stop after the current iteration."""
        self._running = False
        logger.info("mqtt_subscriber_stop_requested")

    async def run(self) -> None:
        """Main loop: connect, subscribe, and process messages.

        Reconnects automatically with exponential backoff on failure.
        """
        logger.info(
            "mqtt_subscriber_starting",
            broker=self._settings.mqtt_broker_host,
            port=self._settings.mqtt_broker_port,
            topics=self._settings.mqtt_topics,
        )

        while self._running:
            try:
                await self._connect_and_subscribe()
            except aiomqtt.MqttError as exc:
                if not self._running:
                    break
                logger.warning(
                    "mqtt_connection_lost",
                    error=str(exc),
                    reconnect_delay=self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * _BACKOFF_MULTIPLIER,
                    _MAX_RECONNECT_DELAY,
                )
            except asyncio.CancelledError:
                logger.info("mqtt_subscriber_cancelled")
                break
            except Exception as exc:
                if not self._running:
                    break
                logger.error(
                    "mqtt_subscriber_unexpected_error",
                    error=str(exc),
                    reconnect_delay=self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * _BACKOFF_MULTIPLIER,
                    _MAX_RECONNECT_DELAY,
                )

        logger.info("mqtt_subscriber_stopped")

    async def _connect_and_subscribe(self) -> None:
        """Establish a connection, subscribe to topics, and process messages."""
        async with aiomqtt.Client(
            hostname=self._settings.mqtt_broker_host,
            port=self._settings.mqtt_broker_port,
        ) as client:
            # Subscribe to all configured topics
            for topic in self._settings.mqtt_topics:
                await client.subscribe(topic)
                logger.info("mqtt_subscribed", topic=topic)

            # Reset backoff on successful connection
            self._reconnect_delay = _BASE_RECONNECT_DELAY
            logger.info("mqtt_connected", broker=self._settings.mqtt_broker_host)

            async for message in client.messages:
                if not self._running:
                    break
                await self._handle_message(message)

    async def _handle_message(self, message: aiomqtt.Message) -> None:
        """Parse an MQTT message and push it through the ingestion pipeline."""
        topic = str(message.topic)

        try:
            # Decode payload
            raw_payload = message.payload
            if isinstance(raw_payload, bytes):
                raw_payload = raw_payload.decode("utf-8")

            data = json.loads(raw_payload)

            # Extract device_id from topic if available
            # Topic pattern: devices/{device_id}/telemetry
            topic_parts = topic.split("/")
            topic_device_id = topic_parts[1] if len(topic_parts) >= 3 else None

            # Build TelemetryReading from the JSON payload.
            # If device_id is not in the payload, try to get it from the topic.
            if "device_id" not in data and topic_device_id:
                data["device_id"] = topic_device_id

            # Ensure required fields have defaults
            if "device_type" not in data:
                data["device_type"] = "unknown"

            if "timestamp" not in data:
                data["timestamp"] = datetime.utcnow().isoformat()

            if "payload" not in data:
                # If the message body has no nested "payload" key, treat the
                # entire data dict (minus known top-level keys) as the payload.
                known_keys = {"device_id", "device_type", "timestamp", "metadata"}
                sensor_data = {k: v for k, v in data.items() if k not in known_keys}
                data["payload"] = sensor_data if sensor_data else {}

            reading = TelemetryReading(**data)

            await ingestion_service.process(
                reading=reading,
                redis_client=self._redis_client,
                protocol="mqtt",
                stream_max_len=self._settings.stream_max_len,
            )

            logger.debug(
                "mqtt_message_processed",
                topic=topic,
                device_id=str(reading.device_id),
            )

        except json.JSONDecodeError as exc:
            logger.warning(
                "mqtt_invalid_json",
                topic=topic,
                error=str(exc),
            )
        except Exception as exc:
            logger.error(
                "mqtt_message_processing_failed",
                topic=topic,
                error=str(exc),
            )
