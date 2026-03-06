"""Prometheus metrics registry and common metrics."""

from prometheus_client import Counter, Histogram, Gauge, Info

# Common metrics shared across services
TELEMETRY_RECEIVED = Counter(
    "telemetry_received_total",
    "Total telemetry readings received",
    ["device_type", "protocol"],
)

TELEMETRY_PROCESSED = Counter(
    "telemetry_processed_total",
    "Total telemetry readings processed",
    ["device_type", "status"],
)

STREAM_MESSAGES_PUBLISHED = Counter(
    "stream_messages_published_total",
    "Messages published to Redis Streams",
    ["stream"],
)

STREAM_MESSAGES_CONSUMED = Counter(
    "stream_messages_consumed_total",
    "Messages consumed from Redis Streams",
    ["stream", "consumer_group"],
)

PROCESSING_LATENCY = Histogram(
    "processing_latency_seconds",
    "Time to process a telemetry reading",
    ["service", "operation"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

INFERENCE_LATENCY = Histogram(
    "inference_latency_seconds",
    "ML inference latency",
    ["model_name"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
)

ANOMALIES_DETECTED = Counter(
    "anomalies_detected_total",
    "Total anomalies detected by ML inference",
    ["device_type", "severity"],
)

ACTIVE_DEVICES = Gauge(
    "active_devices",
    "Number of currently active devices",
    ["device_type"],
)

SERVICE_INFO = Info(
    "service",
    "Service metadata",
)
