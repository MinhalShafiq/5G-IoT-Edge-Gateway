"""Anomaly Report Spark Job.

Analyzes historical anomaly patterns from the anomaly log table, producing
breakdowns by device type, severity, hour of day, and top offending devices.
"""

from __future__ import annotations

import structlog
from pyspark.sql import functions as F
from pyspark.sql.types import TimestampType

from batch_analytics.services.spark_manager import SparkManager

logger = structlog.get_logger(__name__)

DEFAULT_TABLE = "anomaly_events"


def run(spark_manager: SparkManager, params: dict) -> dict:
    """Analyze historical anomaly patterns.

    Params:
        start_time (str, optional): ISO-8601 start of the time range.
        end_time (str, optional): ISO-8601 end of the time range.
        device_type (str, optional): Filter to a specific device type.
        severity (str, optional): Filter to a specific severity level.
        table_name (str, optional): Override the source table name.
        top_n (int, optional): Number of top devices to return (default: 10).

    Returns:
        dict with keys:
            - summary: {total_anomalies, by_device_type, by_severity,
                       by_hour_of_day, top_devices}
            - rows: list of anomaly records
            - rows_processed: int
    """
    table_name = params.get("table_name", DEFAULT_TABLE)
    top_n = params.get("top_n", 10)

    logger.info("anomaly_report starting", params=params)

    # Read anomaly events from PostgreSQL
    df = spark_manager.read_from_postgres(table_name)

    # Ensure timestamp column is the right type
    if "timestamp" in df.columns:
        df = df.withColumn("timestamp", F.col("timestamp").cast(TimestampType()))

    # Apply time range filters
    if params.get("start_time"):
        df = df.filter(F.col("timestamp") >= F.lit(params["start_time"]))
    if params.get("end_time"):
        df = df.filter(F.col("timestamp") <= F.lit(params["end_time"]))

    # Apply optional filters
    if params.get("device_type"):
        df = df.filter(F.col("device_type") == F.lit(params["device_type"]))
    if params.get("severity"):
        df = df.filter(F.col("severity") == F.lit(params["severity"]))

    total_anomalies = df.count()

    # Breakdown by device type
    by_device_type_rows = (
        df.groupBy("device_type")
        .agg(F.count("*").alias("count"))
        .orderBy(F.desc("count"))
        .collect()
    )
    by_device_type = {row["device_type"]: row["count"] for row in by_device_type_rows}

    # Breakdown by severity
    by_severity_rows = (
        df.groupBy("severity")
        .agg(F.count("*").alias("count"))
        .orderBy(F.desc("count"))
        .collect()
    )
    by_severity = {row["severity"]: row["count"] for row in by_severity_rows}

    # Breakdown by hour of day
    by_hour_df = (
        df.withColumn("hour_of_day", F.hour(F.col("timestamp")))
        .groupBy("hour_of_day")
        .agg(F.count("*").alias("count"))
        .orderBy("hour_of_day")
    )
    by_hour_rows = by_hour_df.collect()
    by_hour_of_day = {row["hour_of_day"]: row["count"] for row in by_hour_rows}

    # Top devices by anomaly count
    top_devices_rows = (
        df.groupBy("device_id")
        .agg(
            F.count("*").alias("anomaly_count"),
            F.first("device_type").alias("device_type"),
        )
        .orderBy(F.desc("anomaly_count"))
        .limit(top_n)
        .collect()
    )
    top_devices = [
        {
            "device_id": row["device_id"],
            "device_type": row["device_type"],
            "anomaly_count": row["anomaly_count"],
        }
        for row in top_devices_rows
    ]

    # Build detailed rows for CSV export
    detail_rows = [
        {
            "device_type": dt,
            "count": count,
            "category": "by_device_type",
        }
        for dt, count in by_device_type.items()
    ]
    detail_rows.extend(
        {
            "severity": sev,
            "count": count,
            "category": "by_severity",
        }
        for sev, count in by_severity.items()
    )

    summary = {
        "total_anomalies": total_anomalies,
        "by_device_type": by_device_type,
        "by_severity": by_severity,
        "by_hour_of_day": by_hour_of_day,
        "top_devices": top_devices,
    }

    logger.info(
        "anomaly_report completed",
        total_anomalies=total_anomalies,
        device_types=len(by_device_type),
    )

    return {
        "summary": summary,
        "rows": top_devices,
        "rows_processed": total_anomalies,
    }
