"""Sensor Aggregation Spark Job.

Performs hourly and daily aggregation of sensor telemetry data, computing
statistical summaries (avg, min, max, count, stddev) grouped by time
bucket and device type.
"""

from __future__ import annotations

import structlog
from pyspark.sql import functions as F
from pyspark.sql.types import TimestampType

from batch_analytics.services.spark_manager import SparkManager

logger = structlog.get_logger(__name__)

# Default table name for telemetry readings
DEFAULT_TABLE = "telemetry_readings"


def run(spark_manager: SparkManager, params: dict) -> dict:
    """Aggregate sensor readings by time bucket and device type.

    Params:
        start_time (str, optional): ISO-8601 start of the time range.
        end_time (str, optional): ISO-8601 end of the time range.
        device_type (str, optional): Filter to a specific device type.
        granularity (str, optional): 'hour' or 'day' (default: 'hour').
        table_name (str, optional): Override the source table name.

    Returns:
        dict with keys:
            - summary: {total_rows, unique_devices, unique_types, granularity}
            - rows: list of {period, device_type, avg_value, min_value,
                     max_value, count, stddev_value}
            - rows_processed: int
    """
    table_name = params.get("table_name", DEFAULT_TABLE)
    granularity = params.get("granularity", "hour")

    logger.info(
        "sensor_aggregation starting",
        granularity=granularity,
        params=params,
    )

    # Read telemetry data from PostgreSQL
    df = spark_manager.read_from_postgres(table_name)

    # Ensure timestamp column is the right type
    if "timestamp" in df.columns:
        df = df.withColumn("timestamp", F.col("timestamp").cast(TimestampType()))

    # Filter by time range if specified
    if params.get("start_time"):
        df = df.filter(F.col("timestamp") >= F.lit(params["start_time"]))
    if params.get("end_time"):
        df = df.filter(F.col("timestamp") <= F.lit(params["end_time"]))

    # Filter by device type if specified
    if params.get("device_type"):
        df = df.filter(F.col("device_type") == F.lit(params["device_type"]))

    total_rows = df.count()

    # Determine the time bucket based on granularity
    if granularity == "day":
        df = df.withColumn("period", F.date_trunc("day", F.col("timestamp")))
    else:
        df = df.withColumn("period", F.date_trunc("hour", F.col("timestamp")))

    # Group by time bucket and device_type, compute aggregates
    agg_df = (
        df.groupBy("period", "device_type")
        .agg(
            F.avg("value").alias("avg_value"),
            F.min("value").alias("min_value"),
            F.max("value").alias("max_value"),
            F.count("value").alias("count"),
            F.stddev("value").alias("stddev_value"),
        )
        .orderBy("period", "device_type")
    )

    # Collect results
    result_rows = [row.asDict() for row in agg_df.collect()]

    # Convert any non-serializable types (Decimal, Timestamp) to strings
    for row in result_rows:
        for key, val in row.items():
            if hasattr(val, "isoformat"):
                row[key] = val.isoformat()
            elif val is not None and not isinstance(val, (str, int, float, bool)):
                row[key] = str(val)

    unique_devices = df.select("device_id").distinct().count() if "device_id" in df.columns else 0
    unique_types = df.select("device_type").distinct().count() if "device_type" in df.columns else 0

    summary = {
        "total_rows": total_rows,
        "aggregated_rows": len(result_rows),
        "unique_devices": unique_devices,
        "unique_types": unique_types,
        "granularity": granularity,
    }

    logger.info(
        "sensor_aggregation completed",
        total_rows=total_rows,
        aggregated_rows=len(result_rows),
    )

    return {
        "summary": summary,
        "rows": result_rows,
        "rows_processed": total_rows,
    }
