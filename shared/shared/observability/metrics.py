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
    # Sub-millisecond buckets: most readings are processed in well under 1 ms,
    # and histogram_quantile can only interpolate within a bucket.
    buckets=[0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

INFERENCE_LATENCY = Histogram(
    "inference_latency_seconds",
    "ML inference latency per ONNX Runtime call (one call scores a batch)",
    ["model_name"],
    buckets=[0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
)

INFERENCE_BATCH_SIZE = Histogram(
    "inference_batch_size",
    "Readings scored per ONNX Runtime call",
    buckets=[1, 10, 50, 100, 250, 500, 1000],
)

ANOMALIES_DETECTED = Counter(
    "anomalies_detected_total",
    "Total anomalies detected by ML inference",
    ["device_type", "severity"],
)

ACTIVE_DEVICES = Gauge(
    "active_devices",
    "Number of devices that sent telemetry within the activity window",
    ["device_type"],
)

STREAM_LENGTH = Gauge(
    "redis_stream_length",
    "Number of entries currently held in a Redis Stream",
    ["stream"],
)

CONSUMER_GROUP_LAG = Gauge(
    "redis_consumer_group_lag",
    "Entries in a stream not yet delivered to a consumer group",
    ["stream", "consumer_group"],
)

CONSUMER_GROUP_PENDING = Gauge(
    "redis_consumer_group_pending",
    "Entries delivered to a consumer group but not yet acknowledged",
    ["stream", "consumer_group"],
)

SERVICE_INFO = Info(
    "service",
    "Service metadata",
)
