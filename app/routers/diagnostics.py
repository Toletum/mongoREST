from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import (
    ApiKeyPrincipal,
    authenticate,
    require_global_admin,
    require_global_diagnostics,
)
from app.bson_utils import to_extended_json

router = APIRouter(prefix="/api/v1/diagnostics", tags=["MongoDB diagnostics"])


def get_client(request: Request) -> Any:
    return request.app.state.mongo_client


@router.get("/server-status")
async def server_status(
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    require_global_diagnostics(principal)
    result = await get_client(request).admin.command("serverStatus")
    return to_extended_json(result)


@router.get("/current-operations")
async def current_operations(
    request: Request,
    include_idle: bool = False,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    require_global_diagnostics(principal)
    result = await get_client(request).admin.command({"currentOp": 1, "$all": include_idle})
    return to_extended_json(result)


@router.post("/operations/{opid}/kill")
async def kill_operation(
    opid: int,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    if opid <= 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="opid must be positive")
    require_global_diagnostics(principal)
    require_global_admin(principal)
    result = await get_client(request).admin.command({"killOp": 1, "op": opid})
    return to_extended_json(result)
