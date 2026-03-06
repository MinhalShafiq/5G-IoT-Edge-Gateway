"""IoT Edge Gateway Dashboard — Web UI for managing all services."""

import asyncio
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

# When running inside Docker, the project is mounted at /project.
# When running locally, use the parent directory.
PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parent.parent))
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
COMPOSE_TEST_FILE = PROJECT_ROOT / "docker-compose.test.yml"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

app = FastAPI(title="IoT Edge Gateway Dashboard")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Health URLs use Docker-internal hostnames when running inside Docker,
# localhost when running outside.
_IN_DOCKER = os.path.exists("/.dockerenv")
_HOST = "host.docker.internal" if _IN_DOCKER else "localhost"

SERVICES = {
    "redis": {"health_url": None, "type": "infra"},
    "postgres": {"health_url": None, "type": "infra"},
    "mosquitto": {"health_url": None, "type": "infra"},
    "data-ingestion": {"health_url": f"http://{'data-ingestion' if _IN_DOCKER else 'localhost'}:8001/health", "type": "edge"},
    "data-persistence": {"health_url": None, "type": "edge"},
    "ml-inference": {"health_url": f"http://{'ml-inference' if _IN_DOCKER else 'localhost'}:8004/health", "type": "edge"},
    "prometheus": {"health_url": f"http://{'prometheus' if _IN_DOCKER else 'localhost'}:9090/-/healthy", "type": "monitoring"},
    "grafana": {"health_url": f"http://{'grafana' if _IN_DOCKER else 'localhost'}:3000/api/health", "type": "monitoring"},
    "simulator": {"health_url": None, "type": "simulator"},
}


def _run(cmd: list[str], timeout: int = 120) -> dict:
    """Run a shell command and return stdout/stderr."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=str(PROJECT_ROOT)
        )
        return {"ok": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "Command timed out"}
    except FileNotFoundError as e:
        return {"ok": False, "stdout": "", "stderr": f"Command not found: {e}"}


def _compose_cmd(*args: str) -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE_FILE), "--project-name", "iot-edge-gateway", *args]


def _compose_test_cmd(*args: str) -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE_TEST_FILE), "--project-name", "iot-edge-gateway-test", *args]


# ── Pages ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ── Service Status ──────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    """Get status of all Docker Compose services."""
    result = _run(_compose_cmd("ps", "--format", "json", "-a"))
    services = {}
    if result["ok"] and result["stdout"].strip():
        for line in result["stdout"].strip().splitlines():
            try:
                svc = json.loads(line)
                name = svc.get("Service", svc.get("Name", "unknown"))
                state = svc.get("State", "unknown")
                health = svc.get("Health", "")
                services[name] = {
                    "state": state,
                    "health": health,
                    "status": svc.get("Status", ""),
                }
            except json.JSONDecodeError:
                continue

    # Check HTTP health endpoints for services that have them
    async with httpx.AsyncClient(timeout=3) as client:
        for name, info in SERVICES.items():
            if info["health_url"] and name in services:
                try:
                    resp = await client.get(info["health_url"])
                    services[name]["http_health"] = resp.json() if resp.status_code == 200 else None
                except Exception:
                    services[name]["http_health"] = None

    return {"services": services, "timestamp": datetime.utcnow().isoformat()}


# ── Stack Control ───────────────────────────────────────────────────────────

# Services that can be built/recreated via compose from inside Docker
# (no host bind-mount volumes — only named volumes and build contexts).
_COMPOSE_SERVICES = [
    "redis", "postgres", "mosquitto",
    "data-ingestion", "data-persistence",
    "ml-inference",
]

# Services with host bind-mount configs (prometheus.yml, grafana provisioning)
# must be managed via plain docker start/stop to avoid path-resolution issues
# when running compose from inside a container.
_DOCKER_ONLY = [
    "iot-edge-gateway-prometheus-1",
    "iot-edge-gateway-grafana-1",
]


@app.post("/api/stack/up")
async def stack_up():
    """Start all services (excluding dashboard itself)."""
    result = _run(
        _compose_cmd("up", "--build", "-d", *_COMPOSE_SERVICES),
        timeout=300,
    )
    # Start monitoring containers (already created by start.sh)
    _run(["docker", "start", *_DOCKER_ONLY])
    return _format_result(result)


@app.post("/api/stack/down")
async def stack_down():
    """Stop all services except dashboard."""
    _run(["docker", "stop", *_DOCKER_ONLY])
    result = _run(_compose_cmd("stop", *_COMPOSE_SERVICES))
    return _format_result(result)


@app.post("/api/stack/restart")
async def stack_restart():
    """Restart all services except dashboard."""
    _run(["docker", "stop", *_DOCKER_ONLY])
    _run(_compose_cmd("stop", *_COMPOSE_SERVICES))
    result = _run(
        _compose_cmd("up", "--build", "-d", *_COMPOSE_SERVICES),
        timeout=300,
    )
    _run(["docker", "start", *_DOCKER_ONLY])
    return _format_result(result)


@app.get("/api/stack/logs")
async def stack_logs(service: str | None = None, tail: int = 100):
    """Get service logs."""
    cmd = _compose_cmd("logs", "--tail", str(tail), "--no-color")
    if service:
        cmd.append(service)
    result = _run(cmd)
    return {"logs": result["stdout"] or result["stderr"]}


def _format_result(result: dict) -> dict:
    """Format a command result, extracting errors if any."""
    if result["ok"]:
        return {"ok": True, "stdout": result["stdout"], "stderr": ""}

    # Extract meaningful error lines from stderr
    stderr = result["stderr"] or ""
    error_lines = [
        line for line in stderr.splitlines()
        if any(kw in line.lower() for kw in ("error", "failed", "cannot", "unable", "conflict", "address already in use"))
    ]
    summary = "\n".join(error_lines[:10]) if error_lines else stderr[-500:]
    return {"ok": False, "stdout": result["stdout"], "stderr": summary}


# ── Simulator ───────────────────────────────────────────────────────────────

@app.post("/api/simulator/start")
async def simulator_start(
    num_devices: int = 10,
    interval: float = 5.0,
    anomaly_rate: float = 0.05,
):
    """Start the IoT device simulator."""
    # Set environment for the simulator container via compose env
    env = os.environ.copy()
    env["NUM_DEVICES"] = str(num_devices)
    env["PUBLISH_INTERVAL_SECONDS"] = str(interval)
    env["ANOMALY_RATE"] = str(anomaly_rate)

    try:
        proc = subprocess.run(
            _compose_cmd("--profile", "simulate", "up", "-d", "simulator"),
            capture_output=True, text=True, timeout=120, cwd=str(PROJECT_ROOT), env=env,
        )
        return {"ok": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "Timed out starting simulator"}


@app.post("/api/simulator/stop")
async def simulator_stop():
    """Stop the simulator."""
    result = _run(_compose_cmd("--profile", "simulate", "stop", "simulator"))
    return result


# ── Tests ───────────────────────────────────────────────────────────────────

def _test_runner_cmd(*pytest_args: str) -> list[str]:
    """Build a docker compose run command for the test-runner service."""
    return _compose_cmd(
        "--profile", "test",
        "run", "--rm", "--no-deps", "test-runner",
        "python", "-m", "pytest", *pytest_args,
    )


def _test_runner_cmd_raw(*cmd_args: str) -> list[str]:
    """Build a docker compose run command with an arbitrary command."""
    return _compose_cmd(
        "--profile", "test",
        "run", "--rm", "--no-deps", "test-runner",
        *cmd_args,
    )


@app.post("/api/tests/run")
async def run_tests(suite: str = "all"):
    """Run test suites via the test-runner container.

    suite = all | unit | integration | e2e | load
    """
    # Build the test-runner image first (only rebuilds if changed)
    build_result = _run(
        _compose_cmd("--profile", "test", "build", "test-runner"),
        timeout=300,
    )
    if not build_result["ok"]:
        return {
            "suite": suite,
            "passed": False,
            "output": "Failed to build test-runner image.",
            "errors": build_result["stderr"],
        }

    match suite:
        case "unit":
            cmd = _test_runner_cmd("tests/unit/", "-v", "--tb=short", "--no-header")
        case "integration":
            # test-runner already has access to redis/postgres via docker network
            cmd = _test_runner_cmd("tests/integration/", "-v", "--tb=short", "--no-header")
        case "e2e":
            cmd = _test_runner_cmd("tests/e2e/", "-v", "--tb=short", "--no-header")
        case "load":
            cmd = _test_runner_cmd_raw(
                "python", "-m", "locust",
                "-f", "tests/load/locustfile.py",
                "--host", "http://data-ingestion:8001",
                "--headless",
                "-u", "10", "-r", "2", "-t", "30s",
                "--only-summary",
            )
        case _:
            cmd = _test_runner_cmd("tests/", "-v", "--tb=short", "--no-header")

    result = _run(cmd, timeout=300)

    return {
        "suite": suite,
        "passed": result["ok"],
        "output": result["stdout"] or result["stderr"],
        "errors": result["stderr"] if not result["ok"] else "",
    }


# ── Live Telemetry & Anomaly Feed ──────────────────────────────────────────

_REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0") if _IN_DOCKER else "redis://localhost:6379/0"


async def _get_redis():
    """Get a raw async redis connection for stream reads."""
    import redis.asyncio as aioredis
    return aioredis.from_url(_REDIS_URL, decode_responses=True)


@app.get("/api/telemetry/recent")
async def recent_telemetry(count: int = 20):
    """Read the most recent telemetry entries from the raw_telemetry stream."""
    try:
        r = await _get_redis()
        # XREVRANGE returns newest first
        entries = await r.xrevrange("raw_telemetry", count=count)
        await r.aclose()
        results = []
        for entry_id, data in entries:
            results.append({"id": entry_id, **data})
        return {"readings": results, "count": len(results)}
    except Exception as e:
        return {"readings": [], "count": 0, "error": str(e)}


@app.get("/api/alerts/recent")
async def recent_alerts(count: int = 20):
    """Read the most recent anomaly alerts from the alerts stream."""
    try:
        r = await _get_redis()
        entries = await r.xrevrange("alerts", count=count)
        await r.aclose()
        results = []
        for entry_id, data in entries:
            results.append({"id": entry_id, **data})
        return {"alerts": results, "count": len(results)}
    except Exception as e:
        return {"alerts": [], "count": 0, "error": str(e)}


@app.get("/api/telemetry/stats")
async def telemetry_stats():
    """Get stream lengths and basic stats."""
    try:
        r = await _get_redis()
        tlen = await r.xlen("raw_telemetry")
        alen = await r.xlen("alerts")
        await r.aclose()
        return {"stream_length": tlen, "alert_count": alen}
    except Exception as e:
        return {"stream_length": 0, "alert_count": 0, "error": str(e)}


# ── WebSocket for live updates ──────────────────────────────────────────────

@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    """Push service status every 5 seconds."""
    await websocket.accept()
    try:
        while True:
            status = await get_status()
            await websocket.send_json(status)
            await asyncio.sleep(5)
    except (WebSocketDisconnect, Exception):
        pass


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    """Push live telemetry and alerts every 2 seconds."""
    await websocket.accept()
    last_telemetry_id = "$"
    last_alert_id = "$"
    try:
        r = await _get_redis()
        # Get initial latest IDs
        tinfo = await r.xinfo_stream("raw_telemetry")
        last_telemetry_id = tinfo.get("last-generated-id", "$")
        try:
            ainfo = await r.xinfo_stream("alerts")
            last_alert_id = ainfo.get("last-generated-id", "$")
        except Exception:
            last_alert_id = "0-0"

        while True:
            readings = []
            alerts = []

            # Read new telemetry since last seen
            tentries = await r.xrange("raw_telemetry", min=f"({last_telemetry_id}", count=50)
            for eid, data in tentries:
                readings.append({"id": eid, **data})
                last_telemetry_id = eid

            # Read new alerts since last seen
            try:
                aentries = await r.xrange("alerts", min=f"({last_alert_id}", count=50)
                for eid, data in aentries:
                    alerts.append({"id": eid, **data})
                    last_alert_id = eid
            except Exception:
                pass

            tlen = await r.xlen("raw_telemetry")
            alen = await r.xlen("alerts")

            await websocket.send_json({
                "readings": readings,
                "alerts": alerts,
                "stream_length": tlen,
                "alert_count": alen,
            })
            await asyncio.sleep(2)

        await r.aclose()
    except (WebSocketDisconnect, Exception):
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=9000, reload=True)
