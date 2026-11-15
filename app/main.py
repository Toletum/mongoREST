from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.config import Settings
from app.mongo_errors import handle_mongo_error
from app.routers import diagnostics, keys, resources, system

BOOTSTRAP_RECORD_ID = "bootstrap-admin"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = Settings.from_environment()
    client = AsyncIOMotorClient(
        settings.mongodb_uri,
        appname="mongoREST",
        serverSelectionTimeoutMS=settings.server_selection_timeout_ms,
    )
    try:
        await client.admin.command("ping")
        api_keys = client[settings.mongodb_database]["api_keys"]
        await api_keys.create_index("key", unique=True, name="unique_api_key")

        if await api_keys.count_documents({}) == 0:
            bootstrap_key = str(uuid4())
            record = {
                "_id": BOOTSTRAP_RECORD_ID,
                "key": bootstrap_key,
                "name": "bootstrap-full-access-admin",
                "active": True,
                "permissions": [{"database": "*", "actions": ["*"]}],
            }
            try:
                await api_keys.insert_one(record)
                print(f"mongoREST initial FULL ACCESS API key: {bootstrap_key}", flush=True)
            except DuplicateKeyError:
                # Another replica won the bootstrap insert; never print its credential here.
                pass

        application.state.settings = settings
        application.state.mongo_client = client
    except Exception:
        client.close()
        raise

    try:
        yield
    finally:
        client.close()


app = FastAPI(
    title="mongoREST",
    description="Stateless, API-key authenticated MongoDB management API",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.metrics = {
    "request_count": 0,
    "status_counts": defaultdict(int),
    "total_latency_seconds": 0.0,
}


@app.middleware("http")
async def record_request_metrics(request: Request, call_next: Any) -> Any:
    started_at = time.perf_counter()
    response_status = status.HTTP_500_INTERNAL_SERVER_ERROR
    try:
        response = await call_next(request)
        response_status = response.status_code
        return response
    finally:
        elapsed = time.perf_counter() - started_at
        metrics = request.app.state.metrics
        metrics["request_count"] += 1
        metrics["status_counts"][str(response_status)] += 1
        metrics["total_latency_seconds"] += elapsed


@app.exception_handler(PyMongoError)
async def pymongo_exception_handler(request: Request, error: PyMongoError) -> Any:
    return await handle_mongo_error(request, error)


@app.get("/health", tags=["Operations"])
async def health(request: Request) -> dict[str, str]:
    client = getattr(request.app.state, "mongo_client", None)
    if client is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service is unavailable")
    try:
        await client.admin.command("ping")
    except PyMongoError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service is unhealthy") from None
    return {"status": "ok", "database": "connected"}


app.include_router(system.router)
app.include_router(keys.router)
app.include_router(resources.router)
app.include_router(diagnostics.router)
