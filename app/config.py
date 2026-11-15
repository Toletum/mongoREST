from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str
    mongodb_database: str = "mongoREST"
    server_selection_timeout_ms: int = 5_000

    @classmethod
    def from_environment(cls) -> Settings:
        mongodb_uri = os.environ.get("MONGODB_URI", "").strip()
        if not mongodb_uri:
            raise RuntimeError("MONGODB_URI must be configured")

        database = os.environ.get("MONGODB_DATABASE", "mongoREST").strip()
        reserved_characters = '/\\."$*<>:|?\x00'
        if not database or len(database) > 63:
            raise RuntimeError("MONGODB_DATABASE must be a valid, non-empty MongoDB database name")
        if database in {"admin", "config", "local"} or any(
            character in database for character in reserved_characters
        ):
            raise RuntimeError("MONGODB_DATABASE cannot be a reserved or invalid MongoDB database name")

        return cls(mongodb_uri=mongodb_uri, mongodb_database=database)
