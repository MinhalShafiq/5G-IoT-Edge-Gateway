# Increment 10 -- Big Data Processing

## Goal

Provide PySpark batch analytics on large historical sensor datasets. The batch-analytics service supports ad-hoc queries and scheduled analytical reports over telemetry and anomaly data stored in PostgreSQL. It reads data via JDBC, processes it using Spark SQL and DataFrame operations, and returns results as JSON or downloadable CSV files.

---

## Files Created

### Batch Analytics Service (`services/batch-analytics/`)

This service runs PySpark jobs for offline analytical workloads that are too heavy for real-time stream processing. It provides four pre-built job types: sensor aggregation, anomaly reporting, device health scoring, and trend analysis.

---

### `pyproject.toml`

**Path:** `services/batch-analytics/pyproject.toml`

Project definition for the batch-analytics package. Declares the following runtime dependencies:

- **fastapi** (>=0.104.0) -- Async HTTP framework for the job management API.
- **uvicorn[standard]** (>=0.24.0) -- ASGI server.
- **pyspark** (>=3.5.0) -- Apache Spark Python API for distributed data processing.
- **numpy** (>=1.26.0) -- Numerical computing for trend analysis regression.
- **sqlalchemy[asyncio]** (>=2.0.23) -- Async ORM for PostgreSQL (used by shared init_db).
- **asyncpg** (>=0.29.0) -- PostgreSQL async driver.
- **pydantic-settings** (>=2.1.0) -- Settings management.
- **structlog** (>=23.2.0) -- Structured logging.
- **prometheus-client** (>=0.19.0) -- Metrics exposition.

Uses setuptools as the build backend, exposes a `batch-analytics` console script, and depends on the shared library at `../../shared`.

---

### `Dockerfile`

**Path:** `services/batch-analytics/Dockerfile`

Docker build based on `python:3.11-slim`. The key addition compared to other service Dockerfiles is the installation of **Java 17** (`openjdk-17-jre-headless`), which is required by the PySpark runtime:

1. Sets `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUNBUFFERED=1`.
2. Installs system dependencies: `gcc`, `libpq-dev` (for asyncpg), and `openjdk-17-jre-headless` (for Spark).
3. Sets `JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64`.
4. Installs the shared library, then the batch-analytics package.
5. Exposes port **8007**.
6. Runs uvicorn targeting `batch_analytics.main:app` on port 8007.

---

### `batch_analytics/__init__.py`

**Path:** `services/batch-analytics/batch_analytics/__init__.py`

Empty init file marking the `batch_analytics` directory as a Python package.

---

### `batch_analytics/config.py`

**Path:** `services/batch-analytics/batch_analytics/config.py`

Configuration module extending `BaseServiceSettings` with Spark-specific settings:

| Setting | Default | Description |
|---|---|---|
| `service_name` | `"batch-analytics"` | Service identifier for logs and metrics |
| `http_port` | `8007` | FastAPI HTTP port |
| `spark_master` | `"local[*]"` | Spark master URL; `local[*]` uses all CPU cores |
| `spark_app_name` | `"iot-batch-analytics"` | Application name visible in the Spark UI |
| `spark_driver_memory` | `"2g"` | JVM heap for the Spark driver |
| `spark_executor_memory` | `"2g"` | JVM heap per Spark executor |

The `local[*]` default enables single-machine operation for development and edge deployments. In a cluster environment, this would be changed to a Spark standalone, YARN, or Kubernetes master URL.

---

### `batch_analytics/main.py`

**Path:** `services/batch-analytics/batch_analytics/main.py`

FastAPI application entry point with lifespan management.

**Startup sequence:**
1. Configures structured logging.
2. Initializes Redis client and PostgreSQL connections.
3. Creates a `SparkManager` with the configured Spark settings (lazy session creation -- the SparkSession is not started until the first job runs).
4. Creates a `ResultStore` (in-memory) and a `JobRunner` wired to the SparkManager and ResultStore.
5. Stores all components on `app.state` for dependency injection.

**Shutdown sequence:**
1. Stops the SparkSession (releases JVM resources).
2. Closes Redis and PostgreSQL connections.

Mounts three routers: `health`, `jobs` (under `/api/v1`), and `results` (under `/api/v1`).

**Design pattern:** Lazy initialization. The SparkSession is only created when the first Spark job is submitted, avoiding the overhead of JVM startup if no jobs are run.

---

### `batch_analytics/routers/__init__.py`

**Path:** `services/batch-analytics/batch_analytics/routers/__init__.py`

Empty init file.

---

### `batch_analytics/routers/jobs.py`

**Path:** `services/batch-analytics/batch_analytics/routers/jobs.py`

REST API router mounted at `/api/v1/jobs` for Spark job management. Defines a validation set of allowed job types:

```python
VALID_JOB_TYPES = {
    "sensor_aggregation",
    "anomaly_report",
    "device_health",
    "trend_analysis",
}
```

**Pydantic schemas:**

- **`SparkJobCreate`** -- Request body: `job_type` (string, validated against `VALID_JOB_TYPES`), `params` (dict with optional filters like `start_time`, `end_time`, `device_type`, `granularity`).
- **`SparkJob`** -- Full job representation: `job_id`, `job_type`, `status` (pending/running/completed/failed/cancelled), `params`, `result_summary`, `created_at`, `completed_at`, `error_message`, `rows_processed`.

**Endpoints:**

| Method | Path | Status | Description |
|---|---|---|---|
| `POST` | `/api/v1/jobs/` | 201 | Submit a new Spark job. Validates `job_type`, dispatches to thread pool, returns immediately. |
| `GET` | `/api/v1/jobs/` | 200 | List all submitted jobs with current status, newest first. |
| `GET` | `/api/v1/jobs/{job_id}` | 200 | Get status and result summary of a specific job. 404 if not found. |
| `DELETE` | `/api/v1/jobs/{job_id}` | 204 | Cancel a pending or running job. 409 if already completed/failed. |

The POST endpoint validates the `job_type` against the allowed set and returns a 400 error with the list of valid types if invalid.

---

### `batch_analytics/routers/results.py`

**Path:** `services/batch-analytics/batch_analytics/routers/results.py`

REST API router mounted at `/api/v1/results` for retrieving completed job outputs.

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/results/{job_id}` | Returns the full result dict for a completed job, including `job_id`, `job_type`, `rows_processed`, and `results`. Returns 409 if the job is not yet completed. |
| `GET` | `/api/v1/results/{job_id}/download` | Returns a `StreamingResponse` with `Content-Type: text/csv` and a `Content-Disposition` attachment header. The filename follows the pattern `{job_type}_{job_id}.csv`. Returns 409 if job is not completed. |

**Design pattern:** StreamingResponse for CSV downloads. The CSV is generated in-memory via `ResultStore.to_csv()` and streamed to the client, enabling large result sets to be downloaded without buffering the entire response.

---

### `batch_analytics/routers/health.py`

**Path:** `services/batch-analytics/batch_analytics/routers/health.py`

Health check router created via the shared `create_health_router("batch-analytics")` factory. Exposes `GET /health`.

---

### `batch_analytics/services/__init__.py`

**Path:** `services/batch-analytics/batch_analytics/services/__init__.py`

Empty init file.

---

### `batch_analytics/services/spark_manager.py`

**Path:** `services/batch-analytics/batch_analytics/services/spark_manager.py`

The `SparkManager` class manages the PySpark `SparkSession` lifecycle and provides database I/O methods.

**SparkSession creation (`get_or_create_session()`):**
- Implements a **lazy singleton** pattern: creates the session only on first access.
- Configures the session with:
  - `spark.driver.memory` and `spark.executor.memory` from settings.
  - `spark.jars.packages=org.postgresql:postgresql:42.6.0` -- auto-downloads the PostgreSQL JDBC driver.
  - `spark.sql.adaptive.enabled=true` -- Adaptive Query Execution for better performance.
  - `spark.sql.shuffle.partitions=8` -- tuned for edge-scale data volumes.

**DSN conversion:**
The shared configuration uses asyncpg-style DSNs (`postgresql+asyncpg://user:pass@host:5432/db`), but Spark requires JDBC URLs. Three properties handle the conversion:

- **`postgres_jdbc_url`** -- Converts the full DSN to `jdbc:postgresql://user:pass@host:5432/db`.
- **`postgres_jdbc_url_clean`** -- Strips credentials, producing `jdbc:postgresql://host:5432/db`.
- **`postgres_properties`** -- Extracts `user`, `password`, and `driver` from the DSN for JDBC connection options.

**Data I/O:**
- **`read_from_postgres(table_name, query=None)`** -- Reads a table or subquery via JDBC. If `query` is provided, wraps it as `(query) AS subquery` for push-down filtering. Returns a PySpark DataFrame.
- **`write_to_postgres(df, table_name, mode="append")`** -- Writes a DataFrame to PostgreSQL via JDBC. Supports modes: append, overwrite, ignore, error.
- **`stop()`** -- Stops the SparkSession and sets the reference to None.

**Design pattern:** Singleton with lazy initialization. The SparkSession (and its JVM) is only created when data processing is actually needed. DSN conversion centralizes the asyncpg-to-JDBC translation logic.

---

### `batch_analytics/services/job_runner.py`

**Path:** `services/batch-analytics/batch_analytics/services/job_runner.py`

The `JobRunner` class manages the lifecycle of Spark batch jobs.

**`submit_job(job_create)`:**
1. Creates a `SparkJob` record with a UUID, `pending` status, and UTC timestamp.
2. Stores it in the `_jobs` dict.
3. Dispatches `_execute_job()` to the default thread-pool executor via `loop.run_in_executor()`.
4. Returns the job record immediately.

**`_execute_job(job)` (runs in thread):**
1. Sets status to `running`.
2. Uses a Python `match/case` statement to dispatch to the correct spark_jobs module:
   - `"sensor_aggregation"` -> `sensor_aggregation.run()`
   - `"anomaly_report"` -> `anomaly_report.run()`
   - `"device_health"` -> `device_health.run()`
   - `"trend_analysis"` -> `trend_analysis.run()`
3. Checks for cooperative cancellation after execution.
4. Stores the full result in `ResultStore`, updates the job with `result_summary`, `rows_processed`, and `completed` status.
5. On exception: sets status to `failed` with the error message.

**`cancel_job(job_id)`:** Sets the job status to `cancelled` and records `completed_at`. Cancellation is cooperative -- the job thread checks the status flag at safe points.

**Design pattern:** Command dispatch with match/case. Thread-pool executor bridges the blocking Spark API with the async FastAPI event loop.

---

### `batch_analytics/services/result_store.py`

**Path:** `services/batch-analytics/batch_analytics/services/result_store.py`

In-memory store for Spark job results. The `ResultStore` class provides:

- **`store(job_id, results)`** -- Saves the full result dict.
- **`get(job_id)`** -- Retrieves results or returns `None`.
- **`to_csv(job_id)`** -- Converts results to a CSV string:
  1. Looks for a `"rows"` key containing tabular data (list of dicts).
  2. If found: uses `csv.DictWriter` with the first row's keys as headers.
  3. If rows is a flat list: writes a single `"value"` column.
  4. Fallback: writes the top-level `"summary"` dict as a single-row CSV.
  5. Returns the CSV string, or `None` if no results exist.
- **`delete(job_id)`** -- Removes stored results.

**Design pattern:** Repository pattern. Provides a clean storage abstraction that could be backed by a persistent store (Redis, S3) in production without changing the API.

---

### `batch_analytics/spark_jobs/__init__.py`

**Path:** `services/batch-analytics/batch_analytics/spark_jobs/__init__.py`

Empty init file.

---

### `batch_analytics/spark_jobs/sensor_aggregation.py`

**Path:** `services/batch-analytics/batch_analytics/spark_jobs/sensor_aggregation.py`

Performs time-bucketed aggregation of sensor telemetry data.

**Parameters:** `start_time`, `end_time`, `device_type`, `granularity` (hour or day), `table_name`.

**Processing:**
1. Reads `telemetry_readings` from PostgreSQL via SparkManager.
2. Casts the `timestamp` column to `TimestampType`.
3. Applies optional filters for time range and device type.
4. Creates a `period` column using `date_trunc("hour"|"day", timestamp)`.
5. Groups by `(period, device_type)` and computes:
   - `avg(value)` -- mean sensor reading
   - `min(value)` -- minimum reading
   - `max(value)` -- maximum reading
   - `count(value)` -- number of readings
   - `stddev(value)` -- standard deviation
6. Orders by period and device_type.
7. Collects results and serializes non-primitive types (timestamps, Decimals) to strings.
8. Counts unique devices and device types.

**Returns:** `{summary, rows, rows_processed}` where rows contain `{period, device_type, avg_value, min_value, max_value, count, stddev_value}`.

**Design pattern:** Time-series bucketed aggregation with PySpark SQL functions.

---

### `batch_analytics/spark_jobs/anomaly_report.py`

**Path:** `services/batch-analytics/batch_analytics/spark_jobs/anomaly_report.py`

Analyzes historical anomaly patterns from the `anomaly_events` table.

**Parameters:** `start_time`, `end_time`, `device_type`, `severity`, `table_name`, `top_n` (default 10).

**Processing:**
1. Reads `anomaly_events` from PostgreSQL.
2. Applies optional time range, device type, and severity filters.
3. Computes four breakdowns:
   - **By device type** -- `GROUP BY device_type`, ordered by count descending.
   - **By severity** -- `GROUP BY severity`, ordered by count descending.
   - **By hour of day** -- Extracts `hour(timestamp)`, groups and counts per hour.
   - **Top devices** -- `GROUP BY device_id` with anomaly count and device_type, limited to `top_n`.
4. Builds detail rows for CSV export combining device_type and severity breakdowns.

**Returns:** `{summary: {total_anomalies, by_device_type, by_severity, by_hour_of_day, top_devices}, rows, rows_processed}`.

**Design pattern:** Multi-faceted analytical report with breakdowns along multiple dimensions.

---

### `batch_analytics/spark_jobs/device_health.py`

**Path:** `services/batch-analytics/batch_analytics/spark_jobs/device_health.py`

Computes fleet-wide health scores for all devices by joining telemetry and anomaly data.

**Health score formula (0-100):**
```
score = 100 - (anomaly_rate * 50) - freshness_penalty
```

Where:
- `anomaly_rate = anomaly_count / total_readings` (capped at 1.0)
- `freshness_penalty = min(50, hours_since_last_reading)`

**Processing:**
1. Reads `telemetry_readings` and `anomaly_events` tables.
2. Aggregates telemetry per device: `total_readings`, `last_seen`, `device_type`.
3. Aggregates anomalies per device: `anomaly_count`.
4. **LEFT JOIN** on `device_id` (devices without anomalies get `anomaly_count=0`).
5. Computes `anomaly_rate` capped at 1.0 using `F.least()`.
6. Computes `hours_since_last` capped at 50 using `unix_timestamp` difference.
7. Computes `health_score` floored at 0.0 using `F.greatest()`.
8. Rounds scores and orders by health_score ascending (worst first).

**Health categories:**
- **Healthy** -- score >= 80
- **Degraded** -- 50 <= score < 80
- **Critical** -- score < 50

**Returns:** `{summary: {total_devices, avg_health_score, healthy_count, degraded_count, critical_count}, rows, rows_processed}`.

**Design pattern:** Composite scoring with multi-table join. Combines two independent data sources (telemetry freshness + anomaly rate) into a single health score.

---

### `batch_analytics/spark_jobs/trend_analysis.py`

**Path:** `services/batch-analytics/batch_analytics/spark_jobs/trend_analysis.py`

Detects trends in sensor data using moving averages and linear regression.

**Parameters:** `start_time`, `end_time`, `device_type`, `metric`, `window_size` (hours, default 24), `table_name`.

**Processing:**
1. Reads `telemetry_readings` and adds a `ts_epoch` column (unix timestamp as double).
2. Computes **moving averages** using PySpark window functions:
   ```python
   Window.partitionBy(*group_cols).orderBy("timestamp").rangeBetween(-window_size * 3600, 0)
   ```
   This creates a time-based sliding window of `window_size` hours.
3. Groups by `(device_type, metric)` and collects all timestamps and values.
4. For each group, runs **linear regression** via `_compute_linear_regression()`:
   - Centers timestamps to avoid numerical issues with large epoch values.
   - Computes slope and intercept via ordinary least squares.
   - Computes R-squared for goodness of fit.
   - Classifies trend direction based on the relative slope (normalized by mean value):
     - **stable** -- less than 1% change per hour
     - **upward** -- positive slope above threshold
     - **downward** -- negative slope above threshold
5. Sorts results by absolute slope (strongest trends first).

**Returns:** `{summary: {total_trends, upward_count, downward_count, stable_count}, rows: [{device_type, metric, trend_direction, slope, r_squared, avg_value, data_points}], rows_processed}`.

**Design pattern:** PySpark window functions for streaming aggregation combined with numpy-based linear regression for statistical trend detection.

---

### Spark Base Image

**Path:** `deploy/docker/spark-base.Dockerfile`

A reusable base Docker image for Spark-based services:

1. Based on `python:3.11-slim`.
2. Installs `openjdk-17-jre-headless` for the Spark JVM runtime.
3. Sets `JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64`.
4. Pre-installs `pyspark==3.5.0`.

Service Dockerfiles can use this as a base image (`FROM spark-base:latest`) to avoid reinstalling Java and PySpark in every build.

---

## How Spark Jobs Work

```
1. User submits job via POST /api/v1/jobs
   {job_type: "sensor_aggregation", params: {granularity: "hour", device_type: "temperature_sensor"}}
        |
        v
2. JobRunner creates SparkJob record with UUID and "pending" status
   Returns 201 immediately with job_id
        |
        v
3. JobRunner dispatches _execute_job() to thread-pool executor
   (Spark is blocking -- must not run on the async event loop)
        |
        v
4. match/case routes to the correct spark_jobs module
   SparkManager.get_or_create_session() -- lazy SparkSession creation
        |
        v
5. SparkManager.read_from_postgres() reads data via JDBC
   - DSN converted from asyncpg format to JDBC URL
   - PostgreSQL JDBC driver auto-downloaded by Spark
        |
        v
6. Spark job processes data:
   - sensor_aggregation: groupBy + agg (avg, min, max, count, stddev)
   - anomaly_report: multi-dimensional groupBy breakdowns
   - device_health: JOIN telemetry + anomalies, composite scoring
   - trend_analysis: window functions + linear regression
        |
        v
7. Results stored in ResultStore (in-memory dict)
   Job status updated to "completed" with result_summary and rows_processed
        |
        v
8. User retrieves results via GET /api/v1/results/{job_id}
   Or downloads CSV via GET /api/v1/results/{job_id}/download
```

---

## Design Patterns Summary

| Pattern | Where Used | Purpose |
|---|---|---|
| Lazy singleton | `SparkManager.get_or_create_session()` | Defers expensive JVM startup until first use |
| Thread-pool executor | `JobRunner.submit_job()` | Bridges blocking Spark API with async FastAPI |
| Command dispatch | `JobRunner._execute_job()` | Routes job types to handler modules via match/case |
| DSN conversion | `SparkManager.postgres_jdbc_url` | Translates asyncpg DSNs to JDBC format |
| Repository pattern | `ResultStore` | Clean storage abstraction for job results |
| StreamingResponse | `results.py /download` | Efficient CSV file downloads |
| Time-based windows | `trend_analysis.py` | Sliding-window moving averages via PySpark Window functions |
| Composite scoring | `device_health.py` | Combines anomaly rate + freshness into a single health metric |
