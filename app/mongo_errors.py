from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from pymongo.errors import (
    AutoReconnect,
    ConnectionFailure,
    DuplicateKeyError,
    NetworkTimeout,
    OperationFailure,
    PyMongoError,
    ServerSelectionTimeoutError,
)


async def handle_mongo_error(request: Request, error: PyMongoError) -> JSONResponse:
    del request
    status_code = 500
    detail = "MongoDB operation failed"

    if isinstance(error, (AutoReconnect, ConnectionFailure, NetworkTimeout, ServerSelectionTimeoutError)):
        status_code, detail = 503, "MongoDB is unavailable"
    elif isinstance(error, DuplicateKeyError):
        status_code, detail = 409, "A resource with the same key already exists"
    elif isinstance(error, OperationFailure):
        if error.code == 13:
            status_code, detail = 403, "The MongoDB service account lacks the required privilege"
        elif error.code == 26:
            status_code, detail = 404, "The requested MongoDB resource was not found"
        elif error.code == 48:
            status_code, detail = 409, "The MongoDB resource already exists"
        else:
            status_code, detail = 400, "MongoDB rejected the requested operation"

    return JSONResponse(status_code=status_code, content={"detail": detail})
