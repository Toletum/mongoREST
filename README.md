# mongoREST

A stateless REST API for managing and querying MongoDB, built with FastAPI, Motor, and Pydantic v2. This README describes the implemented routes, authorization rules, and Podman workflow.

> **High impact:** This service can read, modify, and delete database contents and can inspect or terminate MongoDB operations. Do not expose it directly to the public internet. Use it only with MongoDB deployments you own or are authorized to administer, and apply the security guidance below before deployment.

## Capabilities

- API-key authentication with the `X-API-Key` header and database/collection/action-level authorization.
- Database, collection, index, and document management.
- Filtered document queries, aggregation, and execution-plan inspection.
- Database and collection statistics, server status, active-operation inspection, and operation termination.
- API-key creation, listing, revocation, and deletion, restricted to administrator keys.
- Health and per-process operational metrics endpoints.

The service uses FastAPI, Motor (the async MongoDB driver), Pydantic v2, and Python. It keeps request handling stateless: API keys and their current permissions are looked up in MongoDB rather than trusted from a process-local cache. Operational counters are process-local; they are not configuration or authorization state.

## Requirements and local setup

- Podman installed on the host for MongoDB and container workflows. No system-wide Python packages are needed.
- The project virtual environment is `venv/`; local Python dependencies are isolated there. The `Containerfile` installs the same runtime dependencies into `/opt/venv` in the API image.
- The commands below start MongoDB and build/run the API with Podman.

### Start MongoDB with Podman

Use a strong, unique password. This example binds MongoDB to loopback so it is not exposed on other host interfaces.

```sh
podman network create mongorest-net

podman run -d \
  --name mongorest-mongo \
  --network mongorest-net \
  --network-alias mongo \
  -p 127.0.0.1:27017:27017 \
  -e MONGO_INITDB_ROOT_USERNAME=mongorest-root \
  -e MONGO_INITDB_ROOT_PASSWORD=REPLACE_WITH_A_STRONG_PASSWORD \
  docker.io/library/mongo:7 \
  --auth
```

The official image creates the root user in MongoDB's `admin` database. That user has broad privileges, including the administrative commands needed by the development API. Use this configuration for local development only. For a production deployment, create a dedicated MongoDB user with only the permissions the service needs, protect credentials with a secret manager, and restrict network access.

If connecting from a host process rather than an API container, use `localhost:27017` and an authenticated URI such as:

```text
mongodb://mongorest-root:REPLACE_WITH_A_STRONG_PASSWORD@localhost:27017/?authSource=admin
```

For an API container attached to `mongorest-net`, use the hostname `mongo` instead of `localhost`. URI-reserved characters in usernames and passwords must be percent-encoded.

### Build and run the API container

```sh
podman build -t localhost/mongorest-api:dev .

podman run -d \
  --name mongorest-api \
  --network mongorest-net \
  -p 127.0.0.1:8000:8000 \
  -e MONGODB_URI='mongodb://mongorest-root:REPLACE_WITH_A_STRONG_PASSWORD@mongo:27017/?authSource=admin' \
  -e MONGODB_DATABASE=mongoREST \
  localhost/mongorest-api:dev
```

The API requires `MONGODB_URI`; `MONGODB_DATABASE` defaults to `mongoREST`. Podman does not automatically load-balance or scale ordinary `podman run` containers. For multiple stateless API replicas, run separate instances on the same private network and put a suitable reverse proxy/load balancer in front of them. Do not publish the same host port for every replica. MongoDB remains a shared external dependency; API replicas must use the same MongoDB configuration and must not depend on session affinity or local API-key caches.

## Configuration and bootstrap behavior

The API connects to the `mongoREST` database by default and stores credentials in its `api_keys` collection. These are the supported environment settings:

| Setting | Purpose |
| --- | --- |
| `MONGODB_URI` | Authenticated MongoDB connection URI. Required. |
| `MONGODB_DATABASE` | Configuration database; defaults to `mongoREST`. |

On first startup, if `mongoREST.api_keys` is empty, the service creates an active UUID4 API key with full access and prints the credential to standard output once so an administrator can save it. It uses a fixed bootstrap record ID so concurrent replicas race on one insert and only the winning process prints its generated key. Treat that output as a secret: restrict container log access, retrieve it through a trusted channel, and rotate it if it may have been exposed.

API-key records have this shape:

```json
{
  "key": "UUID4 credential string",
  "name": "owner or service identifier",
  "active": true,
  "permissions": [
    { "database": "*", "actions": ["*"] }
  ]
}
```

The API key is a bearer credential. A key with full access should be treated like a MongoDB administrator password. Key-list responses should omit the credential itself; a newly generated credential is returned only at creation time (and the bootstrap credential is printed only on first initialization). Management endpoints use the MongoDB document ID, not the credential, as the key record identifier.

## Authentication and authorization

Send the API key with every protected request:

```http
X-API-Key: <uuid4-api-key>
```

- Missing, malformed, unknown, or inactive credentials return `401 Unauthorized`.
- A valid key without the required database, collection, or action permission returns `403 Forbidden`.
- Permissions are grants: any matching rule may authorize a request. `database: "*"` matches every database; `collections: ["*"]` matches every collection. Omitting `collections` grants the listed actions throughout that database. `actions: ["*"]` grants all action categories.
- A collection-targeted request must match both its database and collection scope. Database-wide operations require a database-wide rule; a collection-scoped rule does not grant database-level stats or create/drop permissions. Collection listing is filtered to the caller's permitted collections.
- API-key management requires an administrator key with `admin` permission on `database: "*"` (or `actions: ["*"]` on `database: "*"`). Ordinary database-scoped administrators cannot manage API keys.
- The built-in MongoDB databases and the configured `MONGODB_DATABASE` control plane (including `api_keys`) are reserved. Generic database, collection, document, index, and aggregation routes cannot access them, even with a full-access key. Use the dedicated key-management routes for API keys.

| Action | Operations |
| --- | --- |
| `read` | List accessible databases/collections/indexes; find documents; read-only aggregations, including every referenced collection; inspect index definitions. |
| `write` | Insert, update, and delete documents. Aggregations that write require `read` on source/referenced collections and `write` on every output collection. |
| `admin` | Create/drop databases, collections, and indexes; manage API keys (with global scope); terminate operations (in addition to `diagnostics`). |
| `diagnostics` | Database/collection statistics, server status, current operations, and query execution statistics. |

For example, a read-only key limited to two collections can be created with rules like:

```json
[
  { "database": "sales", "collections": ["orders", "customers"], "actions": ["read"] }
]
```

A full-access key uses:

```json
[
  { "database": "*", "actions": ["*"] }
]
```

## API reference

Unless noted otherwise, routes are under `/api/v1`, require `X-API-Key`, and return JSON. Examples use `http://localhost:8000`; replace the example credential with a key obtained through the trusted bootstrap or key-creation flow. Set a shell variable for convenience:

```sh
export API_KEY='REPLACE_WITH_YOUR_UUID4_API_KEY'
```

Object IDs and BSON-specific values use MongoDB Extended JSON representation, for example `{"$oid":"65f000000000000000000001"}`. Request payloads accept Extended JSON and responses serialize BSON values using Extended JSON rather than ambiguous strings.

### Health and metrics

`GET /health` is public and returns only a basic process/connection health result; it must not reveal credentials, database names, or internal diagnostics.

```sh
curl -i http://localhost:8000/health
```

`GET /metrics` requires a key with global `diagnostics` permission. It reports basic request counts, latency measurements, and MongoDB connection health for the responding API process. With multiple replicas these values are per-process and reset when a process restarts; scrape each replica or aggregate them in monitoring rather than treating one instance's counters as cluster-wide totals.

```sh
curl -i http://localhost:8000/metrics -H "X-API-Key: $API_KEY"
```

### API-key management

These routes require global administrator permission. Creation returns the credential once; listing never returns credential values. Revocation marks a key inactive, while deletion removes its record.

Create a key:

```sh
curl -i -X POST http://localhost:8000/api/v1/keys \
  -H "X-API-Key: $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"name":"reporting-reader","permissions":[{"database":"sales","collections":["orders"],"actions":["read"]}]}'
```

List key metadata (default page size 100; use `limit` up to 1,000 and `skip` up to 1,000,000 for pagination):

```sh
curl -i http://localhost:8000/api/v1/keys -H "X-API-Key: $API_KEY"
```

Revoke a key by its management record ID:

```sh
curl -i -X POST http://localhost:8000/api/v1/keys/REPLACE_WITH_KEY_RECORD_ID/revoke \
  -H "X-API-Key: $API_KEY"
```

Permanently delete a key record:

```sh
curl -i -X DELETE http://localhost:8000/api/v1/keys/REPLACE_WITH_KEY_RECORD_ID \
  -H "X-API-Key: $API_KEY"
```

### Database management and statistics

`GET /databases` returns only database names the caller is permitted to discover, excluding MongoDB system databases and the protected mongoREST control-plane database. `POST` creates a database using an internal placeholder collection; that placeholder is hidden from the collection list and removed when the first real collection/data is created. `DELETE` drops a database. Creating or dropping requires `admin` on the target database. Database statistics require `diagnostics` and can include the placeholder while the database is otherwise empty.

List accessible databases:

```sh
curl -i http://localhost:8000/api/v1/databases -H "X-API-Key: $API_KEY"
```

Create a database:

```sh
curl -i -X POST http://localhost:8000/api/v1/databases \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"sales"}'
```

Drop a database:

```sh
curl -i -X DELETE http://localhost:8000/api/v1/databases/sales \
  -H "X-API-Key: $API_KEY"
```

Get `dbStats` for a database (including total/data/storage size, collection count, and total index size):

```sh
curl -i http://localhost:8000/api/v1/databases/sales/stats \
  -H "X-API-Key: $API_KEY"
```

### Collection management and statistics

Listing requires `read`; create/drop requires `admin`; collection statistics require `diagnostics`.

List collections:

```sh
curl -i http://localhost:8000/api/v1/databases/sales/collections \
  -H "X-API-Key: $API_KEY"
```

Create a collection:

```sh
curl -i -X POST http://localhost:8000/api/v1/databases/sales/collections \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"orders"}'
```

Drop a collection:

```sh
curl -i -X DELETE http://localhost:8000/api/v1/databases/sales/collections/orders \
  -H "X-API-Key: $API_KEY"
```

Get `collStats` for a collection (document count, collection/storage size, average object size, and index sizes):

```sh
curl -i http://localhost:8000/api/v1/databases/sales/collections/orders/stats \
  -H "X-API-Key: $API_KEY"
```

### Index inspection and management

Listing index definitions requires `read`. Creating or dropping indexes requires `admin`. The API rejects attempts to drop MongoDB's mandatory `_id_` index and validates index options before passing them to MongoDB.

List index definitions and available per-index statistics:

```sh
curl -i http://localhost:8000/api/v1/databases/sales/collections/orders/indexes \
  -H "X-API-Key: $API_KEY"
```

Create an index (keys are field/direction pairs; `1` is ascending and `-1` descending):

```sh
curl -i -X POST http://localhost:8000/api/v1/databases/sales/collections/orders/indexes \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"keys":[["created_at",-1]],"name":"created_at_desc"}'
```

Drop an index by name:

```sh
curl -i -X DELETE http://localhost:8000/api/v1/databases/sales/collections/orders/indexes/created_at_desc \
  -H "X-API-Key: $API_KEY"
```

### Document operations

The document routes below require a collection-scoped permission. Finds and read-only aggregations require `read`; inserts, updates, and deletes require `write`. Empty update/delete filters are rejected unless the request explicitly includes `"allow_all": true`. Responses containing BSON values use Extended JSON.

Insert one document:

```sh
curl -i -X POST http://localhost:8000/api/v1/databases/sales/collections/orders/documents \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"customer_id":"C-100","total":42.50,"status":"new"}'
```

Insert many documents:

```sh
curl -i -X POST http://localhost:8000/api/v1/databases/sales/collections/orders/documents/insert-many \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"documents":[{"customer_id":"C-100","total":42.50},{"customer_id":"C-101","total":19.00}]}'
```

Find documents with filter, projection, sort, limit, and skip. The request body avoids placing arbitrary MongoDB query JSON in a URL. The result limit is capped at 1,000, skip at 1,000,000, and `max_time_ms` at 60 seconds (default 10 seconds):

```sh
curl -i -X POST http://localhost:8000/api/v1/databases/sales/collections/orders/documents/find \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"filter":{"status":"new"},"projection":{"customer_id":1,"total":1},"sort":[["total",-1]],"limit":50,"skip":0}'
```

Update one matching document:

```sh
curl -i -X POST http://localhost:8000/api/v1/databases/sales/collections/orders/documents/update-one \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"filter":{"customer_id":"C-100"},"update":{"$set":{"status":"paid"}},"upsert":false}'
```

Update all matching documents:

```sh
curl -i -X POST http://localhost:8000/api/v1/databases/sales/collections/orders/documents/update-many \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"filter":{"status":"new"},"update":{"$set":{"status":"queued"}},"upsert":false}'
```

Delete one matching document:

```sh
curl -i -X POST http://localhost:8000/api/v1/databases/sales/collections/orders/documents/delete-one \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"filter":{"customer_id":"C-100"}}'
```

Delete all matching documents:

```sh
curl -i -X POST http://localhost:8000/api/v1/databases/sales/collections/orders/documents/delete-many \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"filter":{"status":"cancelled"}}'
```

Run an aggregation. The API checks collection permissions for `$lookup`, `$graphLookup`, and `$unionWith` reads, and checks destination permissions for `$out` and `$merge`. Write pipelines require `read` on the source/referenced collections and `write` on each destination. Diagnostic stages such as `$indexStats` also require `diagnostics`. Execution time and returned results are bounded.

```sh
curl -i -X POST http://localhost:8000/api/v1/databases/sales/collections/orders/documents/aggregate \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"pipeline":[{"$match":{"status":"paid"}},{"$group":{"_id":"$customer_id","order_count":{"$sum":1}}}],"allow_disk_use":false}'
```

### Query execution statistics

`POST /explain` runs a query or aggregation with MongoDB `executionStats` and requires both `read` and `diagnostics` for the target collection. Execution-stat queries can be expensive; constrain their execution time and use them only on authorized databases.

Explain a find query:

```sh
curl -i -X POST http://localhost:8000/api/v1/databases/sales/collections/orders/explain \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"kind":"find","filter":{"status":"paid"},"sort":[["created_at",-1]],"limit":20}'
```

Explain an aggregation:

```sh
curl -i -X POST http://localhost:8000/api/v1/databases/sales/collections/orders/explain \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"kind":"aggregate","pipeline":[{"$match":{"status":"paid"}}]}'
```

### MongoDB diagnostics

Server status, active operations, and operation termination are powerful administrative functions. `server-status` and `current-operations` require global `diagnostics` permission (`database: "*"`); killing an operation additionally requires global `admin`. The MongoDB-connected service account must itself have the corresponding MongoDB privileges.

Read server status and active metrics:

```sh
curl -i http://localhost:8000/api/v1/diagnostics/server-status \
  -H "X-API-Key: $API_KEY"
```

List current operations (set `include_idle=true` to include idle operations):

```sh
curl -i 'http://localhost:8000/api/v1/diagnostics/current-operations?include_idle=false' \
  -H "X-API-Key: $API_KEY"
```

Terminate an operation by its MongoDB `opid`. Validate the identifier and return MongoDB's result; this cannot be undone.

```sh
curl -i -X POST http://localhost:8000/api/v1/diagnostics/operations/REPLACE_WITH_OPID/kill \
  -H "X-API-Key: $API_KEY"
```

## Response and error conventions

The API uses normal HTTP status codes and JSON error bodies with a stable, non-sensitive shape, for example `{"detail":"..."}`. It does not return stack traces, connection URIs, API credentials, or raw internal exception details.

| Status | Meaning |
| --- | --- |
| `200 OK` | Successful read or operation with a response body. |
| `201 Created` | Resource or API key created. |
| `204 No Content` | Successful resource/key deletion with no response body. |
| `400 Bad Request` | Invalid request shape, unsupported option, or invalid BSON input. |
| `401 Unauthorized` | Missing, invalid, malformed, or inactive API key. |
| `403 Forbidden` | Valid key without the required permission. |
| `404 Not Found` | Requested database, collection, key record, or resource does not exist. |
| `409 Conflict` | Duplicate/conflicting resource or index operation. |
| `422 Unprocessable Entity` | Request fails schema validation. |
| `500 Internal Server Error` | Unexpected server failure; details belong in protected server logs only. |
| `503 Service Unavailable` | MongoDB is unavailable or the service cannot reach it. |

## Security and operational guidance

- Keep the API bound to a private network or loopback during development. In production, require TLS at a trusted reverse proxy and firewall the API and MongoDB ports.
- Do not use the MongoDB root account in production. Give the API a dedicated least-privilege MongoDB user; API-level permissions do not replace MongoDB permissions.
- Use separate, scoped API keys per person or service. Rotate and revoke keys promptly. Protect the bootstrap output, container logs, shell history, and backups containing the `api_keys` collection.
- Never log `X-API-Key`, MongoDB URIs, document payloads, or sensitive query values. Avoid returning API credentials from list or error endpoints.
- Bound request bodies, find limits, aggregation stages, execution time, and result sizes. Validate database/collection names and index options; avoid permitting dangerous aggregation write stages through read-only grants.
- Audit key changes and destructive operations without recording secrets. Monitor failed authentication, authorization denials, and database health.
- A stateless API can run multiple replicas, but each request must validate current key state against MongoDB. Do not introduce local authorization caches that allow revoked keys or stale permissions to remain valid across instances.

## Development and tests

The project includes unit tests for permission matching, BSON serialization, and aggregation namespace checks, plus MongoDB integration tests that verify startup creates the default full-access key and that a full-access key can create another full-access key. Install dependencies into the project virtual environment and run the tests with:

```sh
venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
venv/bin/python -m pytest -q
```

The integration tests skip if `MONGODB_URI` is unset. To run the test image against MongoDB on the Podman network instead:

```sh
podman build --target test -t localhost/mongorest-api:test .
podman run --rm --network mongorest-net \
  -e MONGODB_URI='mongodb://mongorest-root:REPLACE_WITH_A_STRONG_PASSWORD@mongo:27017/?authSource=admin' \
  localhost/mongorest-api:test
```

The test image uses the same requirements files and can reach MongoDB when run on `mongorest-net` with an authenticated `MONGODB_URI`.
