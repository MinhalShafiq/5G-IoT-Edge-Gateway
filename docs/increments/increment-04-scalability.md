# Increment 4 -- Performance & Scalability

## Goal

Add auto-scaling, Prometheus monitoring, Grafana dashboards, Alertmanager routing, and load testing infrastructure. This increment ensures the gateway can handle increasing device counts by horizontally scaling the data ingestion service, provides full observability into the telemetry pipeline via pre-configured dashboards, and includes a Locust-based load test suite to validate throughput under stress.

## Data Flow

```
IoT Devices -----> Data Ingestion (2-10 replicas, HPA) -----> Redis Streams
                          |                                        |
                   Prometheus scrape                         Prometheus scrape
                          |                                        |
                          v                                        v
                  Prometheus (TSDB) <---- scrape ---- All Services
                          |
                   +------+------+
                   |             |
                   v             v
                Grafana     Alertmanager
              (dashboards)  (alert routing)
```

Prometheus scrapes all services at 15-second intervals. Grafana renders the pre-built "IoT Gateway Overview" dashboard. Alertmanager routes critical alerts with reduced group wait times for immediate notification.

---

## Files Created

### Horizontal Pod Autoscalers

#### `deploy/k3s/services/data-ingestion/hpa.yaml`
A Kubernetes `HorizontalPodAutoscaler` (autoscaling/v2) for the `data-ingestion` Deployment:

| Parameter | Value | Rationale |
|---|---|---|
| `minReplicas` | 2 | Ensures high availability; at least two pods always run |
| `maxReplicas` | 10 | Upper bound to prevent runaway scaling |
| CPU target | 70% average utilization | Scales up when CPU pressure indicates the service is processing at capacity |
| Memory target | 80% average utilization | Scales up when memory pressure indicates buffer accumulation |

**How it interacts with Redis Streams**: when the HPA scales the data ingestion deployment from 2 to N replicas, each new pod creates its own MQTT subscriber and Redis Streams producer. On the consumer side, the `data_persistence` service uses Redis consumer groups, which naturally partition work across multiple consumers. Each consumer in a group receives a different subset of messages, so adding more persistence worker replicas automatically distributes the write load. No application-level sharding is needed.

---

### Prometheus Stack (`deploy/k3s/monitoring/prometheus/`)

#### `configmap.yaml`
A ConfigMap named `prometheus-config` containing the `prometheus.yml` scrape configuration:

**Global settings**: 15-second scrape and evaluation intervals.

**Scrape jobs** (7 total):

| Job Name | Discovery | Target Port | Metrics Path |
|---|---|---|---|
| `data-ingestion` | Kubernetes pod SD, label filter `app=data-ingestion` | 8001 | `/metrics` |
| `data-persistence` | Kubernetes pod SD, label filter `app=data-persistence` | 9090 | `/metrics` |
| `ml-inference` | Kubernetes pod SD, label filter `app=ml-inference` | 8004 | `/metrics` |
| `device-manager` | Kubernetes pod SD, label filter `app=device-manager` | 8002 | `/metrics` |
| `middleware` | Kubernetes pod SD, label filter `app=middleware` | 8000 | `/metrics` |
| `cloud-api` | Kubernetes pod SD, label filter `app=cloud-api` | 8080 | `/metrics` |
| `redis` | Static config | 6379 | `/metrics` |

Each Kubernetes-based job uses `kubernetes_sd_configs` with `role: pod` and relabel configs to:
1. Keep only pods matching the target app label.
2. Rewrite the `__address__` to use the pod IP with the correct port.

#### `deployment.yaml`
Prometheus Deployment:
- **Image**: `prom/prometheus:v2.48.0`
- **Replicas**: 1 (single instance)
- **Command-line args**: config file at `/etc/prometheus/prometheus.yml`, TSDB storage at `/prometheus`, 7-day retention (`--storage.tsdb.retention.time=7d`), lifecycle API enabled (`--web.enable-lifecycle` for config reloads).
- **Resources**: requests 200m CPU / 256Mi memory; limits 1000m CPU / 1Gi memory.
- **Probes**: HTTP readiness on `/-/ready`, liveness on `/-/healthy`.
- **Volumes**: config from the `prometheus-config` ConfigMap mounted at `/etc/prometheus`, and persistent storage from the `prometheus-pvc` PVC mounted at `/prometheus`.
- **Service account**: `prometheus` (required for Kubernetes service discovery RBAC).

#### `service.yaml`
ClusterIP Service exposing Prometheus on port 9090. Internal access only; Grafana connects to `http://prometheus:9090`.

#### `pvc.yaml`
5Gi ReadWriteOnce PersistentVolumeClaim for Prometheus TSDB data. Ensures metric data survives pod restarts.

---

### Grafana (`deploy/k3s/monitoring/grafana/`)

#### `configmap.yaml`
A ConfigMap named `grafana-config` containing two provisioning files:

**`datasources.yaml`** -- provisions Prometheus as the default datasource:
- Name: `Prometheus`
- Type: `prometheus`
- URL: `http://prometheus:9090`
- Access: `proxy` (Grafana server-side requests)
- Default: `true`
- Editable: `true`

**`dashboards.yaml`** -- configures Grafana's dashboard provider:
- Provider name: `default`
- Folder: `IoT Gateway`
- Type: `file` (loads dashboards from the filesystem)
- Path: `/var/lib/grafana/dashboards`
- `foldersFromFilesStructure: true`

#### `deployment.yaml`
Grafana Deployment:
- **Image**: `grafana/grafana:10.2.0`
- **Replicas**: 1
- **Environment variables**:
  - `GF_SECURITY_ADMIN_USER=admin`
  - `GF_SECURITY_ADMIN_PASSWORD=admin`
  - `GF_USERS_ALLOW_SIGN_UP=false`
- **Resources**: requests 100m CPU / 128Mi memory; limits 500m CPU / 512Mi memory.
- **Probes**: HTTP readiness on `/api/health` port 3000.
- **Volumes**:
  - The `grafana-config` ConfigMap is mounted at both the datasources and dashboards provisioning directories via `subPath`.
  - The `grafana-dashboards` ConfigMap (optional) is mounted at `/var/lib/grafana/dashboards` to load pre-built JSON dashboards.

#### `service.yaml`
NodePort Service exposing Grafana on port 3000. The NodePort type makes Grafana accessible outside the cluster without requiring an ingress controller (suitable for development and edge environments).

#### `dashboards/gateway-overview.json`
A pre-built Grafana dashboard titled "IoT Gateway Overview" with 7 panels:

| Panel | Type | PromQL Query | Description |
|---|---|---|---|
| **Telemetry Ingestion Rate** | Time series | `rate(telemetry_received_total[5m])` | Per-second ingestion rate broken down by `device_type` and `protocol`. Shows whether MQTT, REST, or CoAP traffic is dominant. |
| **Active Devices** | Stat | `sum(active_devices)` | Single number showing the total count of currently active devices across all types. |
| **Anomalies Detected** | Stat | `sum(increase(anomalies_detected_total[1h]))` | Count of anomalies detected in the last hour. A spike here triggers investigation. |
| **Processing Latency (p99)** | Time series | `histogram_quantile(0.99, rate(processing_latency_seconds_bucket[5m]))` | 99th percentile processing latency by service and operation. Indicates whether the ingestion pipeline is keeping up. |
| **ML Inference Latency (p99)** | Time series | `histogram_quantile(0.99, rate(inference_latency_seconds_bucket[5m]))` | 99th percentile ML inference latency by model name. Critical for ensuring real-time anomaly detection meets SLA. |
| **Redis Stream Lengths** | Time series | `stream_messages_published_total - stream_messages_consumed_total` | Estimated backlog per stream. A growing backlog indicates consumers are falling behind producers. |
| **Stream Throughput** | Time series | `rate(stream_messages_published_total[5m])` and `rate(stream_messages_consumed_total[5m])` | Published vs. consumed message rates. When the consumed rate matches the published rate, the system is in steady state. |

Dashboard settings: auto-refresh every 10 seconds, default time range of 1 hour, dark theme, tagged with `iot-gateway` and `overview`.

---

### Alertmanager (`deploy/k3s/monitoring/alertmanager/`)

#### `deployment.yaml`
Alertmanager Deployment:
- **Image**: `prom/alertmanager:v0.26.0`
- **Replicas**: 1
- **Port**: 9093
- **Resources**: requests 50m CPU / 64Mi memory; limits 200m CPU / 128Mi memory.
- **Config volume**: mounted from the `alertmanager-config` ConfigMap at `/etc/alertmanager`.

#### `configmap.yaml`
Alertmanager configuration with severity-based routing:

**Global**: `resolve_timeout: 5m` -- how long to wait before marking an alert as resolved.

**Route tree**:
- **Default route**: receiver `"default"`, grouped by `alertname` and `service`, with `group_wait: 30s`, `group_interval: 5m`, and `repeat_interval: 4h`.
- **Critical override**: alerts matching `severity: critical` are routed to the `"critical"` receiver with `group_wait: 10s` (3x faster than default) for immediate notification.

**Receivers**:
- `"default"` -- placeholder for webhook, email, or Slack integration.
- `"critical"` -- placeholder for high-priority alerting channels (e.g. PagerDuty, OpsGenie).

**Inhibition rules**: a `critical` alert for a given `alertname` and `service` suppresses the corresponding `warning` alert, preventing redundant notifications when the situation has already escalated.

---

### Load Testing (`tests/load/`)

#### `locustfile.py`
A Locust load test that simulates thousands of IoT devices sending telemetry to the data ingestion service.

**Usage**: `locust -f tests/load/locustfile.py --host http://localhost:8001`

**`IoTDeviceUser`** class (extends `HttpUser`):
- **`wait_time`**: between 1 and 5 seconds (random per user, simulating real device intervals).
- **`on_start()`**: assigns each simulated user a unique `device_id` (UUID), a random `device_type` (temperature, vibration, or pressure), and a reading counter.

**Tasks (weighted)**:

| Task | Weight | Description |
|---|---|---|
| `send_telemetry` | 10 | Sends a normal telemetry reading via `POST /api/v1/telemetry`. Generates sensor-appropriate payloads (temperature+humidity, RMS velocity+frequency, or pressure+flow rate). |
| `send_anomalous_telemetry` | 1 | Sends an anomalous reading (~9% of traffic when combined with normal). Temperature spikes to 80-120 deg C, vibration RMS to 10-50 mm/s, pressure drops to 500-800 hPa. |
| `check_stats` | 1 | Queries `GET /api/v1/telemetry/stats` to measure read latency and verify stream health. |
| `health_check` | 1 | Hits `GET /health` to verify the service is responsive under load. |

**Payload generation**: the `_generate_payload()` and `_generate_anomaly_payload()` methods use `match/case` on the device type to produce realistic data with appropriate ranges and distributions (Gaussian noise for normal, extreme values for anomalies).

Each simulated user acts as an independent IoT device, so running Locust with 1,000 users simulates 1,000 concurrent devices. With the weighted task distribution, approximately 77% of requests are normal telemetry, 8% are anomalous, 8% are stats queries, and 8% are health checks.

---

## How It Works Together

### Auto-Scaling Behavior

1. Under normal load (e.g. 100 devices at 5s intervals = 20 msg/s), the data ingestion service runs at 2 replicas (the HPA minimum).
2. As device count grows, CPU utilization on the ingestion pods rises due to MQTT message processing, JSON parsing, and Redis XADD operations.
3. When average CPU utilization exceeds 70% or memory exceeds 80%, the HPA adds replicas (up to 10).
4. New replicas immediately start their own MQTT subscribers (each subscribes to the same `devices/+/telemetry` topic pattern, so Mosquitto distributes messages based on the MQTT broker's behavior) and Redis Stream producers.
5. On the consumer side, the `data_persistence` workers (which also run multiple replicas) read from the `data_persistence` consumer group. Redis Streams automatically distribute pending messages across all consumers in the group, so scaling consumers is zero-configuration.
6. When load decreases, the HPA gradually scales back down (with the default 5-minute stabilization window) to the minimum of 2 replicas.

### Metrics Collection

1. Every Python service exposes Prometheus metrics on its HTTP port at `/metrics` (provided by the `prometheus-client` library).
2. Prometheus discovers service pods via Kubernetes service discovery and scrapes them every 15 seconds.
3. Counters like `telemetry_received_total`, `stream_messages_published_total`, and `anomalies_detected_total` accumulate continuously; Prometheus stores the raw counter values.
4. Histograms like `processing_latency_seconds` and `inference_latency_seconds` track latency distributions with pre-defined buckets (1ms to 1s), enabling percentile calculations at query time.
5. Gauges like `active_devices` report the current state.
6. The Grafana dashboard queries Prometheus using PromQL to compute rates, percentiles, and aggregations in real time.

### Alert Routing

1. When Prometheus evaluates alerting rules (configured separately or via recording rules), firing alerts are sent to Alertmanager.
2. Alertmanager groups alerts by `alertname` and `service` to reduce notification noise.
3. Critical alerts (e.g. "Redis stream backlog exceeding threshold" or "ML inference latency SLA breach") are routed with a 10-second group wait for near-immediate notification.
4. Warning alerts follow the default 30-second group wait and 4-hour repeat interval.
5. Inhibition rules ensure that a critical alert suppresses its corresponding warning, preventing duplicate notifications.

### Load Testing Approach

1. Start the full Docker Compose stack: `make up`.
2. Run Locust: `locust -f tests/load/locustfile.py --host http://localhost:8001`.
3. Open the Locust web UI at `http://localhost:8089` and configure the number of users and ramp-up rate.
4. Each Locust user simulates an IoT device with a unique UUID, sending telemetry every 1-5 seconds.
5. Monitor the Grafana dashboard simultaneously to observe:
   - Telemetry ingestion rate tracking the Locust request rate.
   - Processing latency p99 staying within acceptable bounds.
   - Redis stream lengths remaining stable (not growing unboundedly).
   - Stream throughput showing balanced publish/consume rates.
6. Identify bottlenecks: if the Redis stream backlog grows, the persistence workers need more replicas or a larger batch size. If p99 latency spikes, the ingestion service needs more replicas (or the HPA threshold should be lowered).
7. Validate auto-scaling: run the load test against the Kubernetes deployment and verify that the HPA scales the ingestion pods in response to increased CPU utilization.
