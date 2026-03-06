"""MQTT publisher using paho-mqtt v2 API for telemetry transport."""

from __future__ import annotations

import json

import paho.mqtt.client as mqtt
import structlog

logger = structlog.get_logger(__name__)


class MqttPublisher:
    """Publishes JSON telemetry messages to an MQTT broker.

    Uses the paho-mqtt v2 ``CallbackAPIVersion.VERSION2`` API.
    """

    def __init__(self) -> None:
        self._client: mqtt.Client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="iot-simulator",
            protocol=mqtt.MQTTv5,
        )
        self._connected: bool = False

        # Register callbacks
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: object,
        connect_flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        if reason_code == 0:
            self._connected = True
            logger.info("mqtt_connected", broker=self._broker_address)
        else:
            logger.error(
                "mqtt_connect_failed",
                reason_code=str(reason_code),
            )

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: object,
        disconnect_flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        self._connected = False
        logger.info("mqtt_disconnected", reason_code=str(reason_code))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def connect(self, host: str, port: int) -> None:
        """Connect to the MQTT broker.

        Args:
            host: Broker hostname or IP address.
            port: Broker TCP port (typically 1883).
        """
        self._broker_address = f"{host}:{port}"
        logger.info("mqtt_connecting", broker=self._broker_address)
        self._client.connect(host, port, keepalive=60)
        self._client.loop_start()

    def publish(self, device_id: str, telemetry: dict) -> None:
        """Publish a telemetry message to the broker.

        The message is serialised as JSON and sent to the topic
        ``devices/{device_id}/telemetry`` with QoS 1.

        Args:
            device_id: Unique identifier of the source device.
            telemetry: The telemetry payload dictionary.
        """
        topic = f"devices/{device_id}/telemetry"
        payload = json.dumps(telemetry)
        result = self._client.publish(topic, payload, qos=1)

        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.error(
                "mqtt_publish_failed",
                topic=topic,
                rc=result.rc,
            )
        else:
            logger.debug(
                "mqtt_published",
                topic=topic,
                device_id=device_id,
            )

    def disconnect(self) -> None:
        """Cleanly disconnect from the MQTT broker."""
        logger.info("mqtt_disconnecting")
        self._client.loop_stop()
        self._client.disconnect()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Return ``True`` if the publisher is currently connected."""
        return self._connected
