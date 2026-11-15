from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pymongo.errors import PyMongoError

from app.auth import ApiKeyPrincipal, authenticate, require_global_diagnostics

router = APIRouter()


@router.get("/metrics")
async def metrics(
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, object]:
    require_global_diagnostics(principal)

    metrics_state: dict[str, object] = request.app.state.metrics
    client = request.app.state.mongo_client
    is_database_healthy = True
    try:
        await client.admin.command("ping")
    except PyMongoError:
        is_database_healthy = False

    request_count = int(metrics_state["request_count"])
    total_latency = float(metrics_state["total_latency_seconds"])
    return {
        "request_count": request_count,
        "status_counts": dict(metrics_state["status_counts"]),
        "average_latency_seconds": total_latency / request_count if request_count else 0.0,
        "database_healthy": is_database_healthy,
    }
