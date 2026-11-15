from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from app.main import app

MONGODB_URI = os.environ.get("MONGODB_URI")


@pytest.mark.skipif(not MONGODB_URI, reason="set MONGODB_URI to run the MongoDB integration test")
def test_startup_bootstraps_full_access_key_when_key_collection_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert MONGODB_URI is not None
    database_name = f"mongorest_test_{uuid4().hex[:16]}"
    mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=2_000)
    mongo_client.drop_database(database_name)
    monkeypatch.setenv("MONGODB_DATABASE", database_name)

    try:
        with TestClient(app) as test_client:
            output = capsys.readouterr().out
            key_line = next(
                line for line in output.splitlines()
                if line.startswith("mongoREST initial FULL ACCESS API key: ")
            )
            bootstrap_key = key_line.rsplit(" ", 1)[-1]
            response = test_client.get(
                "/api/v1/databases",
                headers={"X-API-Key": bootstrap_key},
            )

        assert len(bootstrap_key) == 36
        assert response.status_code == 200
        assert mongo_client[database_name]["api_keys"].find_one({"key": bootstrap_key, "active": True}) is not None
    finally:
        mongo_client.drop_database(database_name)
        mongo_client.close()


@pytest.mark.skipif(not MONGODB_URI, reason="set MONGODB_URI to run the MongoDB integration test")
def test_full_access_api_key_can_create_another_full_access_key(monkeypatch: pytest.MonkeyPatch) -> None:
    assert MONGODB_URI is not None
    database_name = f"mongorest_test_{uuid4().hex[:16]}"
    bootstrap_key = str(uuid4())
    mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=2_000)
    api_keys = mongo_client[database_name]["api_keys"]
    api_keys.insert_one({
        "key": bootstrap_key,
        "name": "integration-admin",
        "active": True,
        "permissions": [{"database": "*", "actions": ["*"]}],
    })
    monkeypatch.setenv("MONGODB_DATABASE", database_name)

    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/api/v1/keys",
                headers={"X-API-Key": bootstrap_key},
                json={
                    "name": "created-full-access-key",
                    "permissions": [{"database": "*", "actions": ["*"]}],
                },
            )

        assert response.status_code == 201
        payload = response.json()
        assert payload["name"] == "created-full-access-key"
        assert payload["active"] is True
        assert payload["permissions"] == [{"database": "*", "actions": ["*"]}]
        assert len(payload["key"]) == 36
        assert api_keys.find_one({"key": payload["key"], "active": True}) is not None
    finally:
        mongo_client.drop_database(database_name)
        mongo_client.close()
