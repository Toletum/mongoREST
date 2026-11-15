from __future__ import annotations

import json
from typing import Any

from bson import json_util
from bson.json_util import RELAXED_JSON_OPTIONS
from fastapi import HTTPException, status


def from_extended_json(value: Any) -> Any:
    """Convert Extended JSON sent by a client into native BSON values."""
    try:
        return json_util.loads(json.dumps(value))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid MongoDB Extended JSON value",
        ) from None


def to_extended_json(value: Any) -> Any:
    """Convert BSON values into JSON-safe MongoDB Extended JSON values."""
    return json.loads(json_util.dumps(value, json_options=RELAXED_JSON_OPTIONS))
