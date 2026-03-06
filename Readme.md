# 5G IoT Edge Gateway

## Prerequisites

- **Docker** 24+ with Docker Compose v2

```bash
docker --version && docker compose version
```

---

## Quick Start (One Command)

```bash
bash start.sh
```

This script:
1. Creates `.env` from `.env.example` if missing
2. Cleans up stale containers and checks for port conflicts
3. Builds and starts all services (infrastructure, edge services, monitoring, dashboard)
4. Waits for health checks and prints a summary with URLs

Once done, open **http://localhost:9000** to access the web dashboard.

---

## Manual Start (Step by Step)

If you prefer to start services manually instead of using `start.sh`:

```bash
# 1. Create .env from template (if not already done)
cp .env.example .env

# 2. Build and start all core services
docker compose up --build -d

# 3. Start the simulator (separate profile)
docker compose --profile simulate up -d simulator

# 4. Run tests (separate profile)
docker compose --profile test run --rm test-runner
```

To stop everything:

```bash
docker compose --profile simulate down   # stops simulator + all services
docker compose down -v                   # also removes data volumes
```

---

## Using the Web Dashboard (http://localhost:9000)

The dashboard is a single-page control panel for the entire system.

### Stats Row

Four counters at the top update in real time:
- **Telemetry Readings** — total messages in the raw_telemetry Redis Stream
- **Anomalies Detected** — total entries in the alerts stream
- **Readings / sec** — current throughput
- **Services Running** — number of healthy containers

### Live Telemetry Feed

The left panel shows a scrolling feed of telemetry readings as they arrive from the simulator. Each entry shows the timestamp, device type, device ID, and sensor payload. A green left border indicates normal readings.

### Anomaly Alerts

The right panel shows anomaly detections from the ML inference service. Each alert displays the device ID, severity (warning/critical), and anomaly score. A red left border highlights alerts.

Both panels update automatically via WebSocket — no manual refresh needed.

### Stack Control

- **Start All** — builds and starts all services (excluding the dashboard itself)
- **Stop All** — stops all services
- **Restart** — stops then starts all services
- **View Logs** — fetches combined container logs

Output appears in the terminal box below the buttons.

### Services Panel

Shows all running containers with live health indicators:
- Green dot = running and healthy
- Yellow dot = starting up
- Red dot = stopped or errored

Status auto-refreshes every 5 seconds via WebSocket.

### Simulator

Configure and launch the IoT device fleet simulator:

| Parameter    | Default | Description                                    |
|--------------|---------|------------------------------------------------|
| Devices      | 50      | Number of simulated IoT sensors (max 10,000)   |
| Interval (s) | 2       | Seconds between readings per device             |
| Anomaly Rate | 0.10    | Fraction of readings that are anomalous (0-1)   |

Click **Start Simulator** to launch, **Stop Simulator** to stop. The simulator sends MQTT telemetry through the full pipeline: MQTT -> data-ingestion -> Redis Stream -> ml-inference -> alerts.

### Test Runner

Click a button to run tests inside a dedicated container:
- **All Tests** — full pytest suite (integration + e2e)
- **Integration** — tests the telemetry pipeline (Redis streams, consumer groups)
- **E2E** — end-to-end tests (HTTP POST telemetry, health checks)
- **Load Test** — 30-second Locust load test simulating concurrent IoT devices

Results (pass/fail, full output) appear inline. The first run may take longer as the test container image is built.

### Service Logs

1. Select a service from the dropdown (or leave on "All Services")
2. Set how many tail lines to fetch (default 100)
3. Click **Fetch Logs**

---

## Service Endpoints

| Service             | URL                        | Purpose                                    |
|---------------------|----------------------------|--------------------------------------------|
| **Dashboard**       | http://localhost:9000      | Web control panel with live data feeds     |
| **Grafana**         | http://localhost:3000      | Monitoring dashboards (admin / admin)      |
| **Prometheus**      | http://localhost:9090      | Metrics store                              |
| **Data Ingestion**  | http://localhost:8001      | Telemetry API (MQTT + HTTP)                |
| **API Docs**        | http://localhost:8001/docs | Swagger UI for the ingestion API           |
| **ML Inference**    | (internal, port 8004)      | ONNX anomaly detection (no host port)      |

---

## Architecture Overview

```
Simulator (MQTT)
    |
    v
Mosquitto (MQTT Broker)
    |
    v
Data Ingestion ──> Redis Stream (raw_telemetry) ──> ML Inference (ONNX)
                       |                                  |
                       v                                  v
                  Data Persistence              Redis Stream (alerts)
                       |                                  |
                       v                                  v
                   PostgreSQL                     Dashboard (live feed)
                                                  Prometheus / Grafana
```

---

## Grafana Monitoring (http://localhost:3000)

Login: `admin` / `admin`

Pre-configured with:
- **Prometheus** datasource (auto-provisioned)
- **IoT Gateway Overview** dashboard with panels for:
  - Telemetry ingestion rate per device type and protocol
  - Active devices count
  - Anomalies detected
  - ML inference latency
  - Redis stream lengths and throughput

---

## Environment Variables

Copy `.env.example` to `.env` (done automatically by `start.sh`). Defaults work for local development:

```env
POSTGRES_USER=iot_gateway
POSTGRES_PASSWORD=changeme
REDIS_URL=redis://redis:6379/0
MQTT_BROKER_HOST=mosquitto
JWT_SECRET=change-this-to-a-secure-random-string
LOG_LEVEL=INFO
```

The ML inference threshold is configured in `docker-compose.yml`:

```yaml
ml-inference:
  environment:
    ANOMALY_THRESHOLD: 0.5   # anomaly score above this triggers an alert
```

---

## Project Structure

```
iot-edge-gateway/
├── start.sh                # One-command setup script
├── docker-compose.yml      # All services, monitoring, and dashboard
├── .env.example            # Environment variable template
├── dashboard/              # Web UI (port 9000)
├── services/
│   ├── data-ingestion/     # MQTT + HTTP telemetry ingestion
│   ├── data-persistence/   # Redis -> PostgreSQL writer
│   └── ml-inference/       # ONNX Runtime anomaly detection
├── simulator/              # IoT device fleet simulator
├── shared/                 # Shared models, Redis client, observability
├── monitoring/
│   ├── prometheus/         # Prometheus config
│   └── grafana/            # Grafana provisioning and dashboards
├── tests/                  # Integration, E2E, and load tests
└── docs/                   # Documentation
```