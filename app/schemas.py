from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Action = Literal["read", "write", "admin", "diagnostics", "*"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PermissionRule(StrictModel):
    database: str = Field(min_length=1, max_length=63)
    collections: list[str] | None = None
    actions: list[Action] = Field(min_length=1)

    @field_validator("database")
    @classmethod
    def validate_database_pattern(cls, value: str) -> str:
        if value != "*" and any(character in value for character in '/\\."$*<>:|?\x00'):
            raise ValueError("database contains a MongoDB-reserved character")
        return value

    @field_validator("collections")
    @classmethod
    def validate_collection_patterns(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(not item or "\x00" in item for item in value):
            raise ValueError("collection names must be non-empty and contain no null byte")
        return value


class CreateKeyRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    permissions: list[PermissionRule] = Field(min_length=1)


class CreateDatabaseRequest(StrictModel):
    name: str = Field(min_length=1, max_length=63)


class CreateCollectionRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)


class InsertManyRequest(StrictModel):
    documents: list[dict[str, Any]] = Field(min_length=1, max_length=1000)


class FindRequest(StrictModel):
    filter: dict[str, Any] = Field(default_factory=dict)
    projection: dict[str, int | bool] | None = None
    sort: list[tuple[str, int]] = Field(default_factory=list, max_length=100)
    limit: int = Field(default=100, ge=1, le=1000)
    skip: int = Field(default=0, ge=0, le=1_000_000)
    max_time_ms: int = Field(default=10_000, ge=1, le=60_000)

    @field_validator("sort")
    @classmethod
    def validate_sort(cls, value: list[tuple[str, int]]) -> list[tuple[str, int]]:
        if any(direction not in (-1, 1) for _, direction in value):
            raise ValueError("sort directions must be 1 or -1")
        return value


class UpdateRequest(StrictModel):
    filter: dict[str, Any] = Field(default_factory=dict)
    update: dict[str, Any]
    upsert: bool = False
    allow_all: bool = False

    @field_validator("update")
    @classmethod
    def require_update_operators(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value or any(not key.startswith("$") for key in value):
            raise ValueError("update must use MongoDB update operators, such as $set")
        return value


class DeleteRequest(StrictModel):
    filter: dict[str, Any] = Field(default_factory=dict)
    allow_all: bool = False


class AggregateRequest(StrictModel):
    pipeline: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    allow_disk_use: bool = False
    max_time_ms: int = Field(default=10_000, ge=1, le=60_000)


class ExplainRequest(StrictModel):
    kind: Literal["find", "aggregate"]
    filter: dict[str, Any] = Field(default_factory=dict)
    projection: dict[str, int | bool] | None = None
    sort: list[tuple[str, int]] = Field(default_factory=list, max_length=100)
    limit: int = Field(default=100, ge=1, le=1000)
    pipeline: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    max_time_ms: int = Field(default=10_000, ge=1, le=60_000)

    @model_validator(mode="after")
    def validate_query_kind(self) -> ExplainRequest:
        if self.kind == "aggregate" and not self.pipeline:
            raise ValueError("pipeline is required when kind is 'aggregate'")
        if self.kind == "find" and self.pipeline:
            raise ValueError("pipeline is only valid when kind is 'aggregate'")
        return self


class CreateIndexRequest(StrictModel):
    keys: list[tuple[str, int | str]] = Field(min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    unique: bool = False
    sparse: bool = False
    expire_after_seconds: int | None = Field(default=None, ge=0)
