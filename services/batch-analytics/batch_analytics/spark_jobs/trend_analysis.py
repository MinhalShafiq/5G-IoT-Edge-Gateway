"""Trend Analysis Spark Job.

Detects trends in sensor data using moving averages and linear regression
to identify upward, downward, or stable patterns per device type and metric.
"""

from __future__ import annotations

import structlog
import numpy as np
from pyspark.sql import functions as F
from pyspark.sql.types import TimestampType
from pyspark.sql.window import Window

from batch_analytics.services.spark_manager import SparkManager

logger = structlog.get_logger(__name__)

DEFAULT_TABLE = "telemetry_readings"


def _compute_linear_regression(
    timestamps: list[float], values: list[float]
) -> dict:
    """Compute linear regression using numpy.

    Args:
        timestamps: Unix timestamps as floats.
        values: Sensor values as floats.

    Returns:
        dict with slope, intercept, r_squared, and trend_direction.
    """
    if len(timestamps) < 2:
        return {
            "slope": 0.0,
            "intercept": 0.0,
            "r_squared": 0.0,
            "trend_direction": "stable",
        }

    x = np.array(timestamps, dtype=np.float64)
    y = np.array(values, dtype=np.float64)

    # Normalize x to avoid numerical issues with large timestamps
    x_mean = x.mean()
    x_centered = x - x_mean

    # Linear regression: y = slope * x + intercept
    n = len(x)
    sum_xy = np.sum(x_centered * y)
    sum_xx = np.sum(x_centered * x_centered)

    if sum_xx == 0:
        return {
            "slope": 0.0,
            "intercept": float(y.mean()),
            "r_squared": 0.0,
            "trend_direction": "stable",
        }

    slope = float(sum_xy / sum_xx)
    intercept = float(y.mean() - slope * x_mean)

    # R-squared
    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r_squared = float(1 - ss_res / ss_tot) if ss_tot != 0 else 0.0

    # Determine trend direction based on slope significance
    # Normalize slope relative to the mean value to get a relative rate
    relative_slope = abs(slope * 3600) / max(abs(y.mean()), 1e-10)  # per hour
    if relative_slope < 0.01:  # Less than 1% change per hour
        trend_direction = "stable"
    elif slope > 0:
        trend_direction = "upward"
    else:
        trend_direction = "downward"

    return {
        "slope": round(slope, 8),
        "intercept": round(intercept, 4),
        "r_squared": round(max(0.0, min(1.0, r_squared)), 4),
        "trend_direction": trend_direction,
    }


def run(spark_manager: SparkManager, params: dict) -> dict:
    """Detect trends in sensor data using moving averages and linear regression.

    Params:
        start_time (str, optional): ISO-8601 start of the time range.
        end_time (str, optional): ISO-8601 end of the time range.
        device_type (str, optional): Filter to a specific device type.
        metric (str, optional): Filter to a specific metric/sensor type.
        window_size (int, optional): Moving average window in hours (default: 24).
        table_name (str, optional): Override the source table name.

    Returns:
        dict with keys:
            - summary: {total_trends, upward_count, downward_count, stable_count}
            - rows: list of {device_type, metric, trend_direction, slope,
                    r_squared, avg_value, data_points}
            - rows_processed: int
    """
    table_name = params.get("table_name", DEFAULT_TABLE)
    window_size = params.get("window_size", 24)

    logger.info("trend_analysis starting", params=params)

    # Read telemetry data from PostgreSQL
    df = spark_manager.read_from_postgres(table_name)

    if "timestamp" in df.columns:
        df = df.withColumn("timestamp", F.col("timestamp").cast(TimestampType()))

    # Apply time range filters
    if params.get("start_time"):
        df = df.filter(F.col("timestamp") >= F.lit(params["start_time"]))
    if params.get("end_time"):
        df = df.filter(F.col("timestamp") <= F.lit(params["end_time"]))
    if params.get("device_type"):
        df = df.filter(F.col("device_type") == F.lit(params["device_type"]))
    if params.get("metric"):
        df = df.filter(F.col("metric") == F.lit(params["metric"]))

    total_rows = df.count()

    # Add unix timestamp for regression
    df = df.withColumn("ts_epoch", F.unix_timestamp(F.col("timestamp")).cast("double"))

    # Determine grouping columns: use metric column if it exists
    group_cols = ["device_type"]
    if "metric" in df.columns:
        group_cols.append("metric")

    # Compute moving average using a window function
    window_spec = (
        Window.partitionBy(*group_cols)
        .orderBy("timestamp")
        .rangeBetween(-window_size * 3600, 0)
    )
    df = df.withColumn("moving_avg", F.avg("value").over(window_spec))

    # Collect data per group for regression analysis
    grouped = (
        df.groupBy(*group_cols)
        .agg(
            F.collect_list("ts_epoch").alias("timestamps"),
            F.collect_list("value").alias("values"),
            F.avg("value").alias("avg_value"),
            F.count("*").alias("data_points"),
            F.min("value").alias("min_value"),
            F.max("value").alias("max_value"),
        )
    )

    trend_rows = []
    for row in grouped.collect():
        row_dict = row.asDict()
        timestamps = row_dict.pop("timestamps")
        values = row_dict.pop("values")

        # Perform linear regression
        regression = _compute_linear_regression(timestamps, values)

        trend_row = {
            "device_type": row_dict["device_type"],
            "metric": row_dict.get("metric", "value"),
            "trend_direction": regression["trend_direction"],
            "slope": regression["slope"],
            "r_squared": regression["r_squared"],
            "avg_value": round(float(row_dict["avg_value"]), 4) if row_dict["avg_value"] else 0.0,
            "min_value": round(float(row_dict["min_value"]), 4) if row_dict["min_value"] else 0.0,
            "max_value": round(float(row_dict["max_value"]), 4) if row_dict["max_value"] else 0.0,
            "data_points": int(row_dict["data_points"]),
        }
        trend_rows.append(trend_row)

    # Sort by absolute slope (strongest trends first)
    trend_rows.sort(key=lambda r: abs(r["slope"]), reverse=True)

    # Count trend directions
    upward_count = sum(1 for r in trend_rows if r["trend_direction"] == "upward")
    downward_count = sum(1 for r in trend_rows if r["trend_direction"] == "downward")
    stable_count = sum(1 for r in trend_rows if r["trend_direction"] == "stable")

    summary = {
        "total_trends": len(trend_rows),
        "upward_count": upward_count,
        "downward_count": downward_count,
        "stable_count": stable_count,
    }

    logger.info(
        "trend_analysis completed",
        total_trends=len(trend_rows),
        upward=upward_count,
        downward=downward_count,
        stable=stable_count,
    )

    return {
        "summary": summary,
        "rows": trend_rows,
        "rows_processed": total_rows,
    }
