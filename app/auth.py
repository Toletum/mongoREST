from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import Settings


@dataclass(frozen=True)
class ApiKeyPrincipal:
    record_id: str
    name: str
    permissions: list[dict[str, Any]]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings



def has_permission(
    principal: ApiKeyPrincipal,
    database: str,
    action: str,
    collection: str | None = None,
) -> bool:
    for rule in principal.permissions:
        if not isinstance(rule, dict):
            continue
        rule_database = rule.get("database")
        if rule_database not in ("*", database):
            continue

        actions = rule.get("actions", [])
        if not isinstance(actions, list) or not ("*" in actions or action in actions):
            continue

        collections = rule.get("collections")
        if collection is None and collections is not None:
            continue
        if collection is not None and collections is not None and not (
            isinstance(collections, list) and ("*" in collections or collection in collections)
        ):
            continue
        return True
    return False


def can_discover_database(principal: ApiKeyPrincipal, database: str) -> bool:
    return any(
        isinstance(rule, dict)
        and rule.get("database") in ("*", database)
        and isinstance(rule.get("actions"), list)
        and bool(rule["actions"])
        for rule in principal.permissions
    )


def can_list_collections(principal: ApiKeyPrincipal, database: str) -> bool:
    if has_permission(principal, database, "read"):
        return True
    return any(
        isinstance(rule, dict)
        and rule.get("database") in ("*", database)
        and isinstance(rule.get("actions"), list)
        and ("*" in rule["actions"] or "read" in rule["actions"])
        and isinstance(rule.get("collections"), list)
        and bool(rule["collections"])
        for rule in principal.permissions
    )


def ensure_safe_namespace(
    database: str,
    collection: str | None,
    settings: Settings,
) -> None:
    protected_databases = {"admin", "config", "local", settings.mongodb_database}
    if database in protected_databases:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This database is reserved for MongoDB or mongoREST administration",
        )
    if collection is not None and collection.startswith("system."):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MongoDB system collections cannot be accessed through this endpoint",
        )


def require_permission(
    principal: ApiKeyPrincipal,
    database: str,
    action: str,
    collection: str | None = None,
    settings: Settings | None = None,
) -> None:
    if settings is not None:
        ensure_safe_namespace(database, collection, settings)
    if not has_permission(principal, database, action, collection):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission '{action}' is required for the requested resource",
        )


def require_global_admin(principal: ApiKeyPrincipal) -> None:
    if not has_permission(principal, "*", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Global administrator permission is required",
        )


def require_global_diagnostics(principal: ApiKeyPrincipal) -> None:
    if not has_permission(principal, "*", "diagnostics"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Global diagnostics permission is required",
        )


async def authenticate(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> ApiKeyPrincipal:
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid API key")
    try:
        normalized_key = str(UUID(x_api_key))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid API key") from None

    settings: Settings = request.app.state.settings
    api_keys = request.app.state.mongo_client[settings.mongodb_database]["api_keys"]
    key_record = await api_keys.find_one({"key": normalized_key})
    if not key_record or key_record.get("active") is not True:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid API key")

    return ApiKeyPrincipal(
        record_id=str(key_record.get("_id", "")),
        name=str(key_record.get("name", "")),
        permissions=key_record.get("permissions", []),
    )
