from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.auth import ApiKeyPrincipal, authenticate, get_settings, require_global_admin
from app.config import Settings
from app.schemas import CreateKeyRequest

router = APIRouter(prefix="/api/v1/keys", tags=["API keys"])


def get_key_collection(request: Request, settings: Settings) -> Any:
    return request.app.state.mongo_client[settings.mongodb_database]["api_keys"]


def key_record_query(record_id: str) -> dict[str, Any]:
    if record_id == "bootstrap-admin":
        return {"_id": "bootstrap-admin"}
    if not ObjectId.is_valid(record_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key record not found")
    return {"_id": ObjectId(record_id)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: CreateKeyRequest,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    require_global_admin(principal)
    settings = get_settings(request)
    key_collection = get_key_collection(request, settings)
    key_value = str(uuid4())
    record = {
        "key": key_value,
        "name": body.name,
        "active": True,
        "permissions": [permission.model_dump(exclude_none=True) for permission in body.permissions],
        "created_at": datetime.now(timezone.utc),
    }
    result = await key_collection.insert_one(record)
    return {
        "id": str(result.inserted_id),
        "key": key_value,
        "name": body.name,
        "active": True,
        "permissions": record["permissions"],
    }


@router.get("")
async def list_api_keys(
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
    limit: int = Query(default=100, ge=1, le=1000),
    skip: int = Query(default=0, ge=0, le=1_000_000),
) -> dict[str, Any]:
    require_global_admin(principal)
    settings = get_settings(request)
    cursor = get_key_collection(request, settings).find({}, {"key": 0}).sort("_id", 1)
    records = await cursor.skip(skip).limit(limit + 1).to_list(length=limit + 1)
    has_more = len(records) > limit
    records = records[:limit]
    return {
        "keys": [
            {
                "id": str(record.get("_id", "")),
                "name": record.get("name", ""),
                "active": record.get("active", False),
                "permissions": record.get("permissions", []),
                "created_at": record.get("created_at"),
            }
            for record in records
        ],
        "count": len(records),
        "has_more": has_more,
        "next_skip": skip + len(records) if has_more else None,
    }


@router.post("/{record_id}/revoke")
async def revoke_api_key(
    record_id: str,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    require_global_admin(principal)
    settings = get_settings(request)
    result = await get_key_collection(request, settings).update_one(
        key_record_query(record_id), {"$set": {"active": False}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key record not found")
    return {"id": record_id, "active": False}


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    record_id: str,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> None:
    require_global_admin(principal)
    settings = get_settings(request)
    result = await get_key_collection(request, settings).delete_one(key_record_query(record_id))
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key record not found")
