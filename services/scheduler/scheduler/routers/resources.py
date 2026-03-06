"""Resource reporting and query endpoints.

Edge nodes periodically POST resource reports to this endpoint.  The scheduler
uses these reports to make informed placement decisions.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from scheduler.services.resource_monitor import NodeResources

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/resources", tags=["resources"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ResourceListResponse(BaseModel):
    """List of all known edge nodes and their resource usage."""

    total: int
    nodes: list[NodeResources]


class ResourceReportAccepted(BaseModel):
    """Acknowledgement that a resource report was accepted."""

    node_id: str
    message: str = "report accepted"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=ResourceListResponse)
async def list_resources(request: Request) -> ResourceListResponse:
    """List all known edge nodes and their current resource usage.

    Only returns nodes whose last report is within the staleness threshold.
    """
    monitor = request.app.state.resource_monitor
    resource_map = monitor.get_resource_map()
    nodes = list(resource_map.values())
    return ResourceListResponse(total=len(nodes), nodes=nodes)


@router.get("/{node_id}", response_model=NodeResources)
async def get_node_resources(
    node_id: str,
    request: Request,
) -> NodeResources:
    """Get the resource report for a specific edge node."""
    monitor = request.app.state.resource_monitor
    resource_map = monitor.get_resource_map()
    node = resource_map.get(node_id)
    if node is None:
        raise HTTPException(
            status_code=404,
            detail=f"Node {node_id} not found or stale",
        )
    return node


@router.post("/report", response_model=ResourceReportAccepted, status_code=202)
async def receive_resource_report(
    report: NodeResources,
    request: Request,
) -> ResourceReportAccepted:
    """Receive a resource utilization report from an edge node.

    Edge nodes should call this endpoint periodically (e.g. every 5-10 seconds)
    to keep the scheduler informed of their current capacity.
    """
    monitor = request.app.state.resource_monitor
    monitor.update(report)
    logger.info(
        "resource report received",
        node_id=report.node_id,
        cpu=report.cpu_usage_percent,
        memory=report.memory_usage_percent,
        gpu=report.gpu_usage_percent,
        pending_tasks=report.pending_tasks,
    )
    return ResourceReportAccepted(node_id=report.node_id)
