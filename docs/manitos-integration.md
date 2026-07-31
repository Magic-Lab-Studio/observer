# ManitOS telemetry integration

ManitOS is one supported Observer integration. It uses an additive, versioned,
retry-safe ingestion contract; existing `/v1/traces` clients remain supported.

## Public contract

Send integration telemetry to:

`POST /v1/ingest/manitos/traces`

When `OBSERVATORY_API_KEY` is configured, use the same documented
`Authorization: Bearer <key>` authentication as the rest of the API.

```json
{
  "schema_version": "manitos.telemetry.v1",
  "idempotency_key": "session-example:turn-4:completed",
  "project_id": "manitos",
  "environment": "development",
  "service_instance_id": "integration-instance-01",
  "session_id": "session-example",
  "turn_id": "turn-000004",
  "actor_id_hash": "hmac-sha256:example-digest",
  "trace": {
    "id": "e74ffdfa-d9e9-49fe-9a7e-3ec20eeeff26",
    "name": "manitos.turn",
    "start_time": 1750000000.0,
    "end_time": 1750000002.0,
    "status": "ok",
    "metadata": {"privacy_mode": "metadata_only"}
  },
  "spans": [
    {
      "id": "46e94fc2-70df-48a6-a3a6-d7ce5c23edb4",
      "parent_id": null,
      "name": "llm.generate",
      "span_type": "llm",
      "start_time": 1750000000.2,
      "end_time": 1750000001.4,
      "status": "ok",
      "tokens_input": 120,
      "tokens_output": 48,
      "attributes": {"model": "local-model", "language": "en"}
    }
  ]
}
```

The schema name, route, and response fields are retained as the published
interoperability contract.

## Idempotency

The unique retry identity is `(project_id, idempotency_key)`.

- Replaying an identical request returns `status: duplicate` and writes no rows.
- Reusing a key with different content returns HTTP `409`.
- A new key may update an existing trace or span by UUID.
- A span UUID cannot be moved to another trace.
- A trace UUID cannot be reused by another project.

Successful responses report accepted, updated, duplicate, and rejected span
counts. Validation errors reject the entire envelope; partial writes are never
committed.

## Limits

- One trace per request.
- Between 1 and 500 spans.
- Maximum serialized envelope size: 2 MiB.
- Maximum individual JSON field size: 64 KiB.
- Maximum JSON nesting depth: 8.
- Trace and span IDs must be UUIDs.
- Session identifiers are opaque strings up to 255 characters.
- Unknown fields and unknown schema versions are rejected.

## Privacy baseline

Integrations should emit metadata-only telemetry. Do not send raw prompts,
responses, tool arguments, credentials, personal data, or artifacts. The
`actor_id_hash` field accepts a locally generated keyed hash and must never
contain a raw user identifier. Observer's authentication, rate limits, payload
limits, JSON-depth limits, and idempotency enforcement apply unchanged.

## Correlation and analytics

The trace list supports exact filters for `project_id`, `environment`,
`service_instance_id`, `session_id`, and `turn_id`. The dashboard intentionally
does not render `actor_id_hash`.

The compatibility endpoint `GET /v1/analytics/manitos-quality` provides bounded,
metadata-only aggregate fields for this integration. Existing field names are
retained to avoid breaking clients; they should not be interpreted as ManitOS
production policy or release criteria.

## Runtime configuration

The ManitOS exporter is opt-in. Integration users may configure these published
environment variables in the ManitOS runtime:

```bash
MANITOS_OBSERVER_ENABLED=1
MANITOS_OBSERVER_URL=https://observer.example.test
MANITOS_OBSERVER_API_KEY=
MANITOS_OBSERVER_PROJECT_ID=manitos
MANITOS_OBSERVER_ENVIRONMENT=development
MANITOS_OBSERVER_INSTANCE_ID=integration-instance-01
MANITOS_OBSERVER_ACTOR_HASH_KEY=<local-secret>
```

The exporter remains metadata-only by default. Deployment-specific health URLs,
storage locations, operational thresholds, and delivery implementation details
are intentionally outside this public interoperability document.

## Database migration

From `backend/`:

```bash
alembic upgrade head
```

Alembic reads `DATABASE_URL` at runtime. The released migration is reversible on
SQLite and PostgreSQL and remains unchanged for compatibility.
