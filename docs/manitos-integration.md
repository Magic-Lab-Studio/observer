# ManitOS telemetry ingestion

Observer includes a versioned adapter for metadata-only telemetry ingestion from
compatible agent runtimes. This page documents the stable wire contract for the
ManitOS integration. It is additive: existing `/v1/traces` clients remain
supported.

## Endpoint

`POST /v1/ingest/manitos/traces`

When `OBSERVATORY_API_KEY` is configured, send it as
`Authorization: Bearer <key>` just like the existing API.

```json
{
  "schema_version": "manitos.telemetry.v1",
  "idempotency_key": "session_20260720:turn_4:completed",
  "project_id": "manitos",
  "environment": "development",
  "service_instance_id": "desktop-01",
  "session_id": "session_20260720_abc123",
  "turn_id": "turn_000004",
  "actor_id_hash": "hmac-sha256:...",
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

## Idempotency

The unique retry identity is `(project_id, idempotency_key)`.

- Replaying an identical request returns `status: duplicate` and writes no rows.
- Reusing a key with different content returns HTTP `409`.
- A new key may update an existing trace or span by UUID.
- A span UUID cannot be moved to another trace.
- A trace UUID cannot be reused by another project.

Successful responses report accepted, updated, duplicate, and rejected span counts.
Validation errors reject the entire envelope; partial writes are never committed.

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

The contract supports input and output JSON, but producers should default to
metadata-only telemetry. Prompts, responses, tool arguments, credentials, and
artifacts must not be sent unless the user explicitly enables content capture and
the payload has passed local redaction. `actor_id_hash` is intended for a locally
generated keyed hash, not a raw user identifier.

The reference ManitOS producer is metadata-only: it records safe routing,
language, continuation, latency, and status attributes, and does not serialize
prompts, responses, tool arguments, credentials, artifacts, or raw actor
identifiers. When a local key is configured, the actor identifier is represented
as a local HMAC-SHA256 digest; otherwise `actor_id_hash` is omitted.

## Correlation

Observer stores the envelope's correlation fields (`project_id`, `environment`,
`service_instance_id`, `session_id`, `turn_id`, `schema_version`) on the trace,
so runtimes can group traces by deployment, session, and turn. `actor_id_hash`
remains available to authorized API consumers but is deliberately not rendered
in the dashboard.

## Quality analytics

`GET /v1/analytics/manitos-quality` accepts `hours`, `project_id`, and optional
`environment` query parameters. It aggregates each signal once per turn and
reports error, degraded, truncated, tool-error, TTS-error, and local-fallback
rates; average turn latency and TTFT; plus model and language distributions.
Empty windows return zero-valued metrics.

## Database migration

From `backend/`:

```bash
alembic upgrade head
```

Alembic reads `DATABASE_URL` at runtime. The migration is reversible on both SQLite and
PostgreSQL and adds the correlation columns plus the `ingestion_receipts` table.
