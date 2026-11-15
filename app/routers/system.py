from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from pymongo.errors import PyMongoError

from app.auth import ApiKeyPrincipal, authenticate, require_global_diagnostics

router = APIRouter()


@router.get("/metrics")
async def metrics(
    request: Request,
    principal: Annotated[ApiKeyPrincipal, Depends(authenticate)],
) -> dict[str, object]:
    require_global_diagnostics(principal)

    metrics_state: dict[str, object] = request.app.state.metrics
    client = request.app.state.mongo_client
    is_database_healthy = True
    try:
        await client.admin.command("ping")
    except PyMongoError:
        is_database_healthy = False

    request_count = int(cast(int, metrics_state.get("request_count", 0)))
    total_latency = float(cast(float, metrics_state.get("total_latency_seconds", 0.0)))
    status_counts = metrics_state.get("status_counts", {})

    return {
        "request_count": request_count,
        "status_counts": dict(status_counts) if isinstance(status_counts, dict) else {},
        "average_latency_seconds": total_latency / request_count if request_count else 0.0,
        "database_healthy": is_database_healthy,
    }
