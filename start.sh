#!/usr/bin/env bash
set -uo pipefail  # no -e: we handle errors ourselves

# IoT Edge Gateway — One-command setup
# Starts all services, monitoring (Prometheus + Grafana), and the web dashboard.

cd "$(dirname "$0")"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo "====================================="
echo " 5G IoT Edge Gateway — Starting Up"
echo "====================================="

# ── Pre-flight checks ──────────────────────────────────────────────────────

if ! command -v docker &>/dev/null; then
  echo -e "${RED}Error: docker is not installed.${NC}"
  exit 1
fi

if ! docker compose version &>/dev/null; then
  echo -e "${RED}Error: docker compose v2 is not available.${NC}"
  exit 1
fi

if ! docker info &>/dev/null 2>&1; then
  echo -e "${RED}Error: Docker daemon is not running.${NC}"
  exit 1
fi

# ── 1. Create .env if missing ─────────────────────────────────────────────

if [ ! -f .env ]; then
  echo "[1/4] Creating .env from template..."
  cp .env.example .env
else
  echo "[1/4] .env already exists, skipping."
fi

# ── 2. Clean up stale state ───────────────────────────────────────────────

echo "[2/4] Cleaning up any stale containers..."
docker compose down --remove-orphans 2>/dev/null || true

# Check ports that we bind to the host and warn if occupied
PORTS_TO_CHECK="8001 9000 9090 3000"
BLOCKED=""
for port in $PORTS_TO_CHECK; do
  if ss -tlnH sport = ":$port" 2>/dev/null | grep -q .; then
    BLOCKED="$BLOCKED $port"
  fi
done

if [ -n "$BLOCKED" ]; then
  echo -e "${YELLOW}Warning: The following host ports are already in use:${BLOCKED}${NC}"
  echo "  Trying to identify and stop conflicting containers..."
  for port in $BLOCKED; do
    # Try to find and stop Docker containers using these ports
    cid=$(docker ps -q --filter "publish=$port" 2>/dev/null)
    if [ -n "$cid" ]; then
      echo "  Stopping container using port $port (${cid:0:12})..."
      docker stop "$cid" 2>/dev/null || true
    fi
  done
  # Re-check after cleanup
  STILL_BLOCKED=""
  for port in $BLOCKED; do
    if ss -tlnH sport = ":$port" 2>/dev/null | grep -q .; then
      STILL_BLOCKED="$STILL_BLOCKED $port"
    fi
  done
  if [ -n "$STILL_BLOCKED" ]; then
    echo -e "${YELLOW}Ports still in use by non-Docker processes:${STILL_BLOCKED}${NC}"
    echo "  You may need to stop those processes manually, or they will cause errors."
    echo "  Continuing anyway — services that don't need those ports will still work."
  fi
fi

# ── 3. Build and start everything ─────────────────────────────────────────

echo "[3/4] Building and starting all services..."
BUILD_OUTPUT=$(docker compose up --build -d 2>&1)
BUILD_RC=$?

if [ $BUILD_RC -ne 0 ]; then
  echo -e "${YELLOW}Docker compose exited with warnings/errors:${NC}"
  # Print only the error lines, not the full build log
  echo "$BUILD_OUTPUT" | grep -iE '(error|failed|cannot|unable|conflict)' | head -20
  echo ""
  echo "Checking which services actually started..."
fi

# ── 4. Wait for health checks ─────────────────────────────────────────────

echo "[4/4] Waiting for services to be ready..."

# For infra services (no host ports) we check Docker's own healthcheck status.
# For host-exposed services we curl their HTTP endpoints.
wait_for_container() {
  local name="$1" container="$2" max_wait="${3:-30}"
  echo -n "  $name..."
  local elapsed=0
  while [ $elapsed -lt "$max_wait" ]; do
    local state
    state=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "missing")
    if [ "$state" = "healthy" ]; then
      echo -e " ${GREEN}OK${NC}"
      return 0
    fi
    # If container has no healthcheck, just check it's running
    local running
    running=$(docker inspect --format='{{.State.Running}}' "$container" 2>/dev/null || echo "false")
    if [ "$state" = "missing" ] && [ "$running" = "false" ]; then
      break
    fi
    if [ "$state" = "" ] && [ "$running" = "true" ]; then
      echo -e " ${GREEN}OK${NC} (no healthcheck)"
      return 0
    fi
    sleep 1
    ((elapsed++))
  done
  echo -e " ${RED}FAILED${NC}"
  return 1
}

wait_for_http() {
  local name="$1" url="$2" max_wait="${3:-30}"
  echo -n "  $name..."
  local elapsed=0
  while [ $elapsed -lt "$max_wait" ]; do
    if curl -sf "$url" >/dev/null 2>&1; then
      echo -e " ${GREEN}OK${NC}"
      return 0
    fi
    sleep 1
    ((elapsed++))
  done
  echo -e " ${RED}FAILED${NC}"
  return 1
}

FAILED=0

# Infra — use Docker healthcheck status
wait_for_container "Redis"      "iot-edge-gateway-redis-1"     30 || ((FAILED++))
wait_for_container "PostgreSQL" "iot-edge-gateway-postgres-1"  30 || ((FAILED++))
wait_for_container "Mosquitto"  "iot-edge-gateway-mosquitto-1" 30 || ((FAILED++))

# Host-exposed services — curl from the host
wait_for_http "Data Ingestion"  "http://localhost:8001/health"    45 || ((FAILED++))
wait_for_http "Dashboard"       "http://localhost:9000/"          30 || ((FAILED++))
wait_for_http "Grafana"         "http://localhost:3000/api/health" 30 || ((FAILED++))

# ── Summary ────────────────────────────────────────────────────────────────

echo ""
if [ $FAILED -eq 0 ]; then
  echo -e "${GREEN}=====================================${NC}"
  echo -e "${GREEN} All services are running!${NC}"
  echo -e "${GREEN}=====================================${NC}"
else
  echo -e "${YELLOW}=====================================${NC}"
  echo -e "${YELLOW} Started with $FAILED service(s) failing health checks.${NC}"
  echo -e "${YELLOW}=====================================${NC}"
  echo ""
  echo "Containers status:"
  docker compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}' 2>/dev/null
fi

echo ""
echo " Dashboard (Control Panel) : http://localhost:9000"
echo " Grafana  (Monitoring)     : http://localhost:3000  (admin/admin)"
echo " Prometheus                : http://localhost:9090"
echo " Data Ingestion API        : http://localhost:8001"
echo " API Docs (Swagger)        : http://localhost:8001/docs"
echo ""
echo " Open http://localhost:9000 to manage everything from one place."
echo ""
