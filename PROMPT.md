# mongoREST

API REST to full access to mongodb

## Environment
- Do not install anything on the host operating system: apt, yay, pacman, etc.
- You have Podman available. Install everything you need using Podman (including MongoDB).
- Use Python with **FastAPI**, **motor** (async MongoDB driver), and **Pydantic v2**.
- Create a virtual env in `venv/`
- Add all python libs that you need in `requirements.txt` (fastapi, uvicorn, motor, pydantic, bson/pymongo, etc.)

## Architecture & Scalability
- **Horizontal Scalability (Stateless):** The FastAPI application must be fully stateless so it can be horizontally scaled (multiple instances behind a load balancer or container replicas) without session or memory affinity issues. All dynamic configurations (such as API keys) must be resolved against MongoDB, avoiding local in-memory caching that breaks multi-instance setups.

## Prerequisites
- Spin up a MongoDB container using Podman for testing (make sure to map port 27017, configure root credentials, and ensure the user has the necessary roles/permissions to run diagnostic and administration commands like `currentOp`, `killOp`, `serverStatus`, `dbStats`, and `collStats`).
- Provide the exact `podman run` command (and/or a `Containerfile` / `podman-compose.yml`) to deploy both MongoDB and the FastAPI app (with support to scale replicas) if needed.

## Architecture & Database Config
- Use a database named `mongoREST` in MongoDB to store configuration and API keys.
- Store APIKeys in a collection named `api_keys` inside `mongoREST` with a clear structure:
  - `key`: UUID string (e.g., UUID4)
  - `name`: string (owner/identifier)
  - `active`: boolean
  - `permissions`: list of access rules defining database access and actions (e.g., `{"database": "*", "actions": ["*"]}` for FULL ACCESS, or granular rules like `{"database": "my_db", "collections": ["*"], "actions": ["read", "write", "admin", "diagnostics"]}`).
- Include an initialization script or startup bootstrap routine that creates a default `FULL ACCESS` admin UUID API key if `mongoREST.api_keys` is empty (printing it to stdout on first run).

## Tasks
- **FastAPI Core & Auth:**
  - Use modern FastAPI `lifespan` context manager to handle the async `AsyncIOMotorClient` connection pool cleanly across requests.
  - Auth using APIKEY via HTTP header (`X-API-Key`).
  - Dependency injection to validate the UUID API key against `mongoREST.api_keys` and enforce database/action permissions (`401 Unauthorized` if missing/invalid, `403 Forbidden` if lacking permissions for the target DB or action).
  - Include endpoints to manage API keys themselves (create, list, revoke/delete UUID keys) restricted to `FULL ACCESS` / `admin` keys.

- **Database & Collection Management:**
  - Create, list, and delete databases.
  - Create, list, and delete collections within a database.
  - Storage & Index Inspection:
    - Database stats (`dbStats`): total size, data size, storage size, number of collections, and total index size.
    - Collection stats (`collStats`) & Index details (`index_information()` / `$indexStats`): document count, collection size, storage size, average object size, total index size, individual index sizes, and index definitions/keys.
    - Basic index management (list, create, and drop indexes on a collection).

- **Document CRUD (Data Operations):**
  - Add documents (`insertOne`, `insertMany`).
  - Update documents (`updateOne`, `updateMany`).
  - Delete documents (`deleteOne`, `deleteMany`).
  - Properly serialize/deserialize MongoDB `ObjectId` and BSON types to/from JSON.

- **Advanced Queries:**
  - `find`: support filter query, projection, sort, limit, and skip.
  - `aggregate`: execute aggregation pipelines.
  - `executionStats`: run `.explain("executionStats")` on queries/aggregations to inspect index usage and performance.

- **MongoDB Metrics & Diagnostics:**
  - Server status and active metrics (`db.command("serverStatus")`).
  - List currently running queries, updates, and active processes (`currentOp` via `admin` database command).
  - Ability to kill/stop blocked or slow operations (`killOp` by `opid`).

- **API Operational Metrics:**
  - Add basic API telemetry/metrics endpoint (e.g., `/metrics` or `/health` tracking request counts, latency, and DB connection health).

## Constraints
- Just create a clean, modular FastAPI backend (e.g., `app/main.py`, routers, auth dependencies, `requirements.txt`, `Containerfile`, and `README.md` with `curl` examples for every endpoint).
- **Do not create any UI.**

## Tests
- create a APIKEY with full access
