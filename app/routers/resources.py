from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pymongo.errors import OperationFailure

from app.auth import (
    ApiKeyPrincipal,
    authenticate,
    can_discover_database,
    can_list_collections,
    ensure_safe_namespace,
    get_settings,
    has_permission,
    require_permission,
)
from app.bson_utils import from_extended_json, to_extended_json
from app.config import Settings
from app.schemas import (
    AggregateRequest,
    CreateCollectionRequest,
    CreateDatabaseRequest,
    CreateIndexRequest,
    DeleteRequest,
    ExplainRequest,
    FindRequest,
    InsertManyRequest,
    UpdateRequest,
)

router = APIRouter(prefix="/api/v1", tags=["MongoDB resources"])
PLACEHOLDER_COLLECTION = "__mongorest_placeholder__"
PROTECTED_DATABASES = {"admin", "config", "local"}
MAX_AGGREGATE_RESULTS = 1000


def get_client(request: Request) -> Any:
    return request.app.state.mongo_client


def validate_database_name(name: str) -> str:
    if not name or len(name) > 63 or any(character in name for character in '/\\."$*<>:|?\x00'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid MongoDB database name")
    return name


def validate_collection_name(name: str) -> str:
    if not name or "\x00" in name or name.startswith("system."):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid MongoDB collection name")
    if name == PLACEHOLDER_COLLECTION:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This collection name is reserved")
    return name


def collection_for(request: Request, database: str, collection: str) -> Any:
    return get_client(request)[database][collection]


async def remove_database_placeholder(request: Request, database_name: str) -> None:
    database = get_client(request)[database_name]
    if PLACEHOLDER_COLLECTION in await database.list_collection_names():
        await database.drop_collection(PLACEHOLDER_COLLECTION)


def check_namespace_permission(
    principal: ApiKeyPrincipal,
    settings: Settings,
    database: str,
    action: str,
    collection: str | None = None,
) -> None:
    require_permission(principal, database, action, collection, settings)


def require_selective_filter(filter_value: dict[str, Any], allow_all: bool) -> None:
    if not filter_value and not allow_all:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An empty filter requires allow_all=true",
        )


def namespace_from_spec(spec: Any, default_database: str) -> tuple[str, str] | None:
    if isinstance(spec, str):
        return default_database, spec
    if not isinstance(spec, dict):
        return None

    collection_name = spec.get("coll", spec.get("collection"))
    if not isinstance(collection_name, str):
        return None
    database_name = spec.get("db", default_database)
    if not isinstance(database_name, str):
        return None
    return database_name, collection_name


def pipeline_accesses(
    pipeline: list[dict[str, Any]],
    database_name: str,
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    read_namespaces: set[tuple[str, str]] = set()
    write_namespaces: set[tuple[str, str]] = set()

    for stage in pipeline:
        lookup = stage.get("$lookup")
        if isinstance(lookup, dict):
            namespace = namespace_from_spec(lookup.get("from"), database_name)
            if namespace is not None:
                read_namespaces.add(namespace)
                nested_pipeline = lookup.get("pipeline", [])
                if isinstance(nested_pipeline, list):
                    nested_reads, nested_writes = pipeline_accesses(nested_pipeline, namespace[0])
                    read_namespaces.update(nested_reads)
                    write_namespaces.update(nested_writes)

        graph_lookup = stage.get("$graphLookup")
        if isinstance(graph_lookup, dict):
            namespace = namespace_from_spec(graph_lookup.get("from"), database_name)
            if namespace is not None:
                read_namespaces.add(namespace)

        union = stage.get("$unionWith")
        if isinstance(union, (str, dict)):
            namespace = namespace_from_spec(union, database_name)
            if namespace is not None:
                read_namespaces.add(namespace)
                if isinstance(union, dict) and isinstance(union.get("pipeline"), list):
                    nested_reads, nested_writes = pipeline_accesses(union["pipeline"], namespace[0])
                    read_namespaces.update(nested_reads)
                    write_namespaces.update(nested_writes)

        output = stage.get("$out")
        if output is not None:
            namespace = namespace_from_spec(output, database_name)
            if namespace is not None:
                write_namespaces.add(namespace)

        merge = stage.get("$merge")
        if isinstance(merge, dict):
            namespace = namespace_from_spec(merge.get("into"), database_name)
            if namespace is not None:
                write_namespaces.add(namespace)

        facet = stage.get("$facet")
        if isinstance(facet, dict):
            for nested_pipeline in facet.values():
                if isinstance(nested_pipeline, list):
                    nested_reads, nested_writes = pipeline_accesses(nested_pipeline, database_name)
                    read_namespaces.update(nested_reads)
                    write_namespaces.update(nested_writes)

    return read_namespaces, write_namespaces


def pipeline_requires_diagnostics(pipeline: list[dict[str, Any]]) -> bool:
    diagnostic_stages = {"$collStats", "$indexStats", "$planCacheStats", "$currentOp", "$listSessions", "$listLocalSessions"}
    for stage in pipeline:
        if diagnostic_stages.intersection(stage):
            return True
        lookup = stage.get("$lookup")
        if (
            isinstance(lookup, dict)
            and isinstance(lookup.get("pipeline"), list)
            and pipeline_requires_diagnostics(lookup["pipeline"])
        ):
            return True
        union = stage.get("$unionWith")
        if (
            isinstance(union, dict)
            and isinstance(union.get("pipeline"), list)
            and pipeline_requires_diagnostics(union["pipeline"])
        ):
            return True
        facet = stage.get("$facet")
        if isinstance(facet, dict) and any(
            pipeline_requires_diagnostics(nested)
            for nested in facet.values()
            if isinstance(nested, list)
        ):
            return True
    return False


@router.get("/databases")
async def list_databases(
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, list[str]]:
    settings = get_settings(request)
    names = await get_client(request).list_database_names()
    return {
        "databases": sorted(
            name for name in names
            if name not in PROTECTED_DATABASES
            and name != settings.mongodb_database
            and can_discover_database(principal, name)
        )
    }


@router.post("/databases", status_code=status.HTTP_201_CREATED)
async def create_database(
    body: CreateDatabaseRequest,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    database_name = validate_database_name(body.name)
    settings = get_settings(request)
    if database_name in PROTECTED_DATABASES or database_name == settings.mongodb_database:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This database name is reserved")
    check_namespace_permission(principal, settings, database_name, "admin")
    if database_name in await get_client(request).list_database_names():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Database already exists")
    database = get_client(request)[database_name]
    await database.create_collection(PLACEHOLDER_COLLECTION)
    return {"name": database_name, "created": True}


@router.delete("/databases/{database_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_database(
    database_name: str,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> None:
    database_name = validate_database_name(database_name)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "admin")
    if database_name not in await get_client(request).list_database_names():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database not found")
    await get_client(request).drop_database(database_name)


@router.get("/databases/{database_name}/stats")
async def database_stats(
    database_name: str,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    database_name = validate_database_name(database_name)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "diagnostics")
    if database_name not in await get_client(request).list_database_names():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database not found")
    result = await get_client(request)[database_name].command("dbStats")
    return to_extended_json(result)


@router.get("/databases/{database_name}/collections")
async def list_collections(
    database_name: str,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, list[str]]:
    database_name = validate_database_name(database_name)
    settings = get_settings(request)
    ensure_safe_namespace(database_name, None, settings)
    if not can_list_collections(principal, database_name):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Read permission is required for at least one collection in this database",
        )
    names = await get_client(request)[database_name].list_collection_names()
    return {
        "collections": sorted(
            name for name in names
            if name != PLACEHOLDER_COLLECTION
            and not name.startswith("system.")
            and has_permission(principal, database_name, "read", name)
        )
    }


@router.post("/databases/{database_name}/collections", status_code=status.HTTP_201_CREATED)
async def create_collection(
    database_name: str,
    body: CreateCollectionRequest,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, str]:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(body.name)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "admin", collection_name)
    database = get_client(request)[database_name]
    await database.create_collection(collection_name)
    if PLACEHOLDER_COLLECTION in await database.list_collection_names():
        await database.drop_collection(PLACEHOLDER_COLLECTION)
    return {"database": database_name, "collection": collection_name}


@router.delete("/databases/{database_name}/collections/{collection_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    database_name: str,
    collection_name: str,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> None:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(collection_name)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "admin", collection_name)
    database = get_client(request)[database_name]
    if collection_name not in await database.list_collection_names():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")
    await database.drop_collection(collection_name)


@router.get("/databases/{database_name}/collections/{collection_name}/stats")
async def collection_stats(
    database_name: str,
    collection_name: str,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(collection_name)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "diagnostics", collection_name)
    result = await get_client(request)[database_name].command("collStats", collection_name)
    return to_extended_json(result)


@router.get("/databases/{database_name}/collections/{collection_name}/indexes")
async def list_indexes(
    database_name: str,
    collection_name: str,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(collection_name)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "read", collection_name)
    collection = collection_for(request, database_name, collection_name)
    definitions = await collection.index_information()
    try:
        index_stats = await collection.aggregate([{"$indexStats": {}}]).to_list(length=1000)
        stats_available = True
    except OperationFailure as error:
        if error.code not in (13, 59):
            raise
        index_stats = []
        stats_available = False
    return to_extended_json({
        "indexes": definitions,
        "index_stats": index_stats,
        "index_stats_available": stats_available,
    })


@router.post("/databases/{database_name}/collections/{collection_name}/indexes", status_code=status.HTTP_201_CREATED)
async def create_index(
    database_name: str,
    collection_name: str,
    body: CreateIndexRequest,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, str]:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(collection_name)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "admin", collection_name)
    options: dict[str, Any] = {"unique": body.unique, "sparse": body.sparse}
    if body.name is not None:
        options["name"] = body.name
    if body.expire_after_seconds is not None:
        options["expireAfterSeconds"] = body.expire_after_seconds
    result = await collection_for(request, database_name, collection_name).create_index(body.keys, **options)
    await remove_database_placeholder(request, database_name)
    return {"name": result}


@router.delete("/databases/{database_name}/collections/{collection_name}/indexes/{index_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_index(
    database_name: str,
    collection_name: str,
    index_name: str,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> None:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(collection_name)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "admin", collection_name)
    if index_name == "_id_":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The mandatory _id_ index cannot be dropped")
    indexes = await collection_for(request, database_name, collection_name).index_information()
    if index_name not in indexes:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Index not found")
    await collection_for(request, database_name, collection_name).drop_index(index_name)


@router.post("/databases/{database_name}/collections/{collection_name}/documents", status_code=status.HTTP_201_CREATED)
async def insert_one(
    database_name: str,
    collection_name: str,
    document: dict[str, Any],
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(collection_name)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "write", collection_name)
    result = await collection_for(request, database_name, collection_name).insert_one(from_extended_json(document))
    await remove_database_placeholder(request, database_name)
    return {"inserted_id": to_extended_json(result.inserted_id)}


@router.post("/databases/{database_name}/collections/{collection_name}/documents/insert-many", status_code=status.HTTP_201_CREATED)
async def insert_many(
    database_name: str,
    collection_name: str,
    body: InsertManyRequest,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(collection_name)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "write", collection_name)
    documents = [from_extended_json(document) for document in body.documents]
    result = await collection_for(request, database_name, collection_name).insert_many(documents)
    await remove_database_placeholder(request, database_name)
    return {"inserted_ids": to_extended_json(result.inserted_ids)}


@router.post("/databases/{database_name}/collections/{collection_name}/documents/find")
async def find_documents(
    database_name: str,
    collection_name: str,
    body: FindRequest,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(collection_name)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "read", collection_name)
    cursor = collection_for(request, database_name, collection_name).find(
        from_extended_json(body.filter), body.projection
    )
    if body.sort:
        cursor = cursor.sort(body.sort)
    if body.skip:
        cursor = cursor.skip(body.skip)
    documents = await cursor.max_time_ms(body.max_time_ms).limit(body.limit).to_list(length=body.limit)
    return {"documents": to_extended_json(documents), "count": len(documents)}


@router.post("/databases/{database_name}/collections/{collection_name}/documents/update-one")
async def update_one(
    database_name: str,
    collection_name: str,
    body: UpdateRequest,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(collection_name)
    require_selective_filter(body.filter, body.allow_all)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "write", collection_name)
    result = await collection_for(request, database_name, collection_name).update_one(
        from_extended_json(body.filter), from_extended_json(body.update), upsert=body.upsert
    )
    await remove_database_placeholder(request, database_name)
    return {"matched_count": result.matched_count, "modified_count": result.modified_count, "upserted_id": to_extended_json(result.upserted_id)}


@router.post("/databases/{database_name}/collections/{collection_name}/documents/update-many")
async def update_many(
    database_name: str,
    collection_name: str,
    body: UpdateRequest,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(collection_name)
    require_selective_filter(body.filter, body.allow_all)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "write", collection_name)
    result = await collection_for(request, database_name, collection_name).update_many(
        from_extended_json(body.filter), from_extended_json(body.update), upsert=body.upsert
    )
    await remove_database_placeholder(request, database_name)
    return {"matched_count": result.matched_count, "modified_count": result.modified_count, "upserted_id": to_extended_json(result.upserted_id)}


@router.post("/databases/{database_name}/collections/{collection_name}/documents/delete-one")
async def delete_one(
    database_name: str,
    collection_name: str,
    body: DeleteRequest,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, int]:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(collection_name)
    require_selective_filter(body.filter, body.allow_all)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "write", collection_name)
    result = await collection_for(request, database_name, collection_name).delete_one(from_extended_json(body.filter))
    return {"deleted_count": result.deleted_count}


@router.post("/databases/{database_name}/collections/{collection_name}/documents/delete-many")
async def delete_many(
    database_name: str,
    collection_name: str,
    body: DeleteRequest,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, int]:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(collection_name)
    require_selective_filter(body.filter, body.allow_all)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "write", collection_name)
    result = await collection_for(request, database_name, collection_name).delete_many(from_extended_json(body.filter))
    return {"deleted_count": result.deleted_count}


@router.post("/databases/{database_name}/collections/{collection_name}/documents/aggregate")
async def aggregate_documents(
    database_name: str,
    collection_name: str,
    body: AggregateRequest,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(collection_name)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "read", collection_name)
    read_namespaces, write_namespaces = pipeline_accesses(body.pipeline, database_name)
    requires_diagnostics = pipeline_requires_diagnostics(body.pipeline)
    if requires_diagnostics:
        check_namespace_permission(principal, settings, database_name, "diagnostics", collection_name)
    for referenced_database, referenced_collection in read_namespaces:
        referenced_database = validate_database_name(referenced_database)
        referenced_collection = validate_collection_name(referenced_collection)
        check_namespace_permission(
            principal, settings, referenced_database, "read", referenced_collection
        )
        if requires_diagnostics:
            check_namespace_permission(
                principal, settings, referenced_database, "diagnostics", referenced_collection
            )
    for target_database, target_collection in write_namespaces:
        target_database = validate_database_name(target_database)
        target_collection = validate_collection_name(target_collection)
        check_namespace_permission(principal, settings, target_database, "write", target_collection)

    pipeline = from_extended_json(body.pipeline)
    cursor = collection_for(request, database_name, collection_name).aggregate(
        pipeline,
        allowDiskUse=body.allow_disk_use,
        maxTimeMS=body.max_time_ms,
    )
    results = await cursor.to_list(length=MAX_AGGREGATE_RESULTS + 1)
    is_truncated = len(results) > MAX_AGGREGATE_RESULTS
    if is_truncated:
        results = results[:MAX_AGGREGATE_RESULTS]
    if write_namespaces:
        await remove_database_placeholder(request, database_name)
    return {"results": to_extended_json(results), "count": len(results), "truncated": is_truncated}


@router.post("/databases/{database_name}/collections/{collection_name}/explain")
async def explain_query(
    database_name: str,
    collection_name: str,
    body: ExplainRequest,
    request: Request,
    principal: ApiKeyPrincipal = Depends(authenticate),
) -> dict[str, Any]:
    database_name = validate_database_name(database_name)
    collection_name = validate_collection_name(collection_name)
    settings = get_settings(request)
    check_namespace_permission(principal, settings, database_name, "read", collection_name)
    check_namespace_permission(principal, settings, database_name, "diagnostics", collection_name)
    if body.kind == "aggregate":
        _, write_namespaces = pipeline_accesses(body.pipeline, database_name)
        if write_namespaces:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Explain only supports read-only aggregations")
        read_namespaces, _ = pipeline_accesses(body.pipeline, database_name)
        for referenced_database, referenced_collection in read_namespaces:
            referenced_database = validate_database_name(referenced_database)
            referenced_collection = validate_collection_name(referenced_collection)
            check_namespace_permission(
                principal, settings, referenced_database, "read", referenced_collection
            )
            check_namespace_permission(
                principal, settings, referenced_database, "diagnostics", referenced_collection
            )

    if body.kind == "find":
        operation: dict[str, Any] = {
            "find": collection_name,
            "filter": from_extended_json(body.filter),
            "limit": body.limit,
            "maxTimeMS": body.max_time_ms,
        }
        if body.projection is not None:
            operation["projection"] = body.projection
        if body.sort:
            operation["sort"] = dict(body.sort)
    else:
        operation = {
            "aggregate": collection_name,
            "pipeline": from_extended_json(body.pipeline),
            "cursor": {},
            "maxTimeMS": body.max_time_ms,
        }
    result = await get_client(request)[database_name].command(
        "explain", operation, verbosity="executionStats"
    )
    return to_extended_json(result)
