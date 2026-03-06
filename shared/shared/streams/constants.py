"""Stream names and consumer group names used across services."""


class StreamName:
    RAW_TELEMETRY = "raw_telemetry"
    ALERTS = "alerts"
    DEVICE_EVENTS = "device_events"
    INFERENCE_RESULTS = "inference_results"


class ConsumerGroup:
    ML_INFERENCE = "ml_inference"
    DATA_PERSISTENCE = "data_persistence"
    ALERT_HANDLER = "alert_handler"
