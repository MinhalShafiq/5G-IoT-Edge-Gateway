"""Device Health Spark Job.

Computes fleet-wide health scores for all devices based on anomaly rate,
data freshness, and total reading volume.
"""

from __future__ import annotations

import structlog
from pyspark.sql import functions as F
from pyspark.sql.types import TimestampType

from batch_analytics.services.spark_manager import SparkManager

logger = structlog.get_logger(__name__)

DEFAULT_TELEMETRY_TABLE = "telemetry_readings"
DEFAULT_ANOMALY_TABLE = "anomaly_events"


def run(spark_manager: SparkManager, params: dict) -> dict:
    """Compute health scores for all devices.

    Health score formula (0-100):
        score = 100 - (anomaly_rate * 50) - freshness_penalty

    Where:
        - anomaly_rate = anomaly_count / total_readings (capped at 1.0)
        - freshness_penalty = min(50, hours_since_last_reading)

    Params:
        start_time (str, optional): ISO-8601 start of the time range.
        end_time (str, optional): ISO-8601 end of the time range.
        device_type (str, optional): Filter to a specific device type.
        telemetry_table (str, optional): Override telemetry table name.
        anomaly_table (str, optional): Override anomaly table name.

    Returns:
        dict with keys:
            - summary: {total_devices, avg_health_score, healthy_count,
                       degraded_count, critical_count}
            - rows: list of {device_id, device_type, health_score,
                    anomaly_rate, last_seen, total_readings, anomaly_count}
            - rows_processed: int
    """
    telemetry_table = params.get("telemetry_table", DEFAULT_TELEMETRY_TABLE)
    anomaly_table = params.get("anomaly_table", DEFAULT_ANOMALY_TABLE)

    logger.info("device_health starting", params=params)

    # Read telemetry readings
    telemetry_df = spark_manager.read_from_postgres(telemetry_table)
    if "timestamp" in telemetry_df.columns:
        telemetry_df = telemetry_df.withColumn(
            "timestamp", F.col("timestamp").cast(TimestampType())
        )

    # Apply time range filters to telemetry
    if params.get("start_time"):
        telemetry_df = telemetry_df.filter(
            F.col("timestamp") >= F.lit(params["start_time"])
        )
    if params.get("end_time"):
        telemetry_df = telemetry_df.filter(
            F.col("timestamp") <= F.lit(params["end_time"])
        )
    if params.get("device_type"):
        telemetry_df = telemetry_df.filter(
            F.col("device_type") == F.lit(params["device_type"])
        )

    total_telemetry = telemetry_df.count()

    # Aggregate telemetry per device
    device_stats = (
        telemetry_df.groupBy("device_id")
        .agg(
            F.count("*").alias("total_readings"),
            F.max("timestamp").alias("last_seen"),
            F.first("device_type").alias("device_type"),
        )
    )

    # Read anomaly events and count per device
    anomaly_df = spark_manager.read_from_postgres(anomaly_table)
    if "timestamp" in anomaly_df.columns:
        anomaly_df = anomaly_df.withColumn(
            "timestamp", F.col("timestamp").cast(TimestampType())
        )

    if params.get("start_time"):
        anomaly_df = anomaly_df.filter(
            F.col("timestamp") >= F.lit(params["start_time"])
        )
    if params.get("end_time"):
        anomaly_df = anomaly_df.filter(
            F.col("timestamp") <= F.lit(params["end_time"])
        )

    anomaly_counts = (
        anomaly_df.groupBy("device_id")
        .agg(F.count("*").alias("anomaly_count"))
    )

    # Join telemetry stats with anomaly counts
    joined = device_stats.join(anomaly_counts, on="device_id", how="left")
    joined = joined.fillna(0, subset=["anomaly_count"])

    # Compute anomaly rate (capped at 1.0)
    joined = joined.withColumn(
        "anomaly_rate",
        F.least(
            F.col("anomaly_count") / F.col("total_readings"),
            F.lit(1.0),
        ),
    )

    # Compute freshness penalty: hours since last reading (capped at 50)
    joined = joined.withColumn(
        "hours_since_last",
        F.least(
            (F.unix_timestamp(F.current_timestamp()) - F.unix_timestamp(F.col("last_seen"))) / 3600.0,
            F.lit(50.0),
        ),
    )

    # Compute health score: 100 - (anomaly_rate * 50) - freshness_penalty
    joined = joined.withColumn(
        "health_score",
        F.greatest(
            F.lit(0.0),
            F.lit(100.0) - (F.col("anomaly_rate") * 50.0) - F.col("hours_since_last"),
        ),
    )

    # Round the health score
    joined = joined.withColumn("health_score", F.round(F.col("health_score"), 1))
    joined = joined.withColumn("anomaly_rate", F.round(F.col("anomaly_rate"), 4))

    # Select final columns and order by health score
    result_df = (
        joined.select(
            "device_id",
            "device_type",
            "health_score",
            "anomaly_rate",
            "last_seen",
            "total_readings",
            "anomaly_count",
        )
        .orderBy(F.asc("health_score"))
    )

    result_rows = [row.asDict() for row in result_df.collect()]

    # Serialize timestamps
    for row in result_rows:
        for key, val in row.items():
            if hasattr(val, "isoformat"):
                row[key] = val.isoformat()
            elif val is not None and not isinstance(val, (str, int, float, bool)):
                row[key] = str(val)

    total_devices = len(result_rows)
    avg_health = (
        sum(r["health_score"] for r in result_rows) / total_devices
        if total_devices > 0
        else 0.0
    )

    # Categorize devices by health status
    healthy_count = sum(1 for r in result_rows if r["health_score"] >= 80)
    degraded_count = sum(1 for r in result_rows if 50 <= r["health_score"] < 80)
    critical_count = sum(1 for r in result_rows if r["health_score"] < 50)

    summary = {
        "total_devices": total_devices,
        "avg_health_score": round(avg_health, 1),
        "healthy_count": healthy_count,
        "degraded_count": degraded_count,
        "critical_count": critical_count,
    }

    logger.info(
        "device_health completed",
        total_devices=total_devices,
        avg_health_score=round(avg_health, 1),
    )

    return {
        "summary": summary,
        "rows": result_rows,
        "rows_processed": total_telemetry,
    }
