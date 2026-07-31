# ManitOS telemetry ingestion

The `manitOS-observer` branch exposes a versioned, retry-safe ingestion contract for
ManitOS runtimes. It is additive: existing `/v1/traces` clients remain supported.

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
- ManitOS `session_id` values are opaque strings up to 255 characters.
- Unknown fields and unknown schema versions are rejected.

## Privacy baseline

The contract supports input and output JSON, but ManitOS exporters should default to
metadata-only telemetry. Prompts, responses, tool arguments, credentials, and artifacts
must not be sent unless the user explicitly enables content capture and the payload has
passed local redaction. `actor_id_hash` is intended for a locally generated keyed hash,
not a raw user identifier.

## ManitOS exporter (phases 2-3)

ManitOS now includes an opt-in background exporter for this contract. Enable it in
the ManitOS process with:

```bash
MANITOS_OBSERVER_ENABLED=1
MANITOS_OBSERVER_URL=http://127.0.0.1:8000
MANITOS_OBSERVER_API_KEY=
MANITOS_OBSERVER_PROJECT_ID=manitos
MANITOS_OBSERVER_ENVIRONMENT=development
MANITOS_OBSERVER_INSTANCE_ID=desktop-01
MANITOS_OBSERVER_ACTOR_HASH_KEY=<local-secret>
```

The exporter is disabled by default. It emits one trace per completed or failed turn,
uses a bounded in-memory queue, retries only transient HTTP/network failures, and
flushes during adapter shutdown. Queue saturation or Observer unavailability never
blocks or fails the conversation path.

The current producer is intentionally metadata-only. It records safe routing,
language, continuation, latency, and status attributes, but does not serialize prompts,
responses, tool arguments, credentials, artifacts, or raw actor identifiers. When
`MANITOS_OBSERVER_ACTOR_HASH_KEY` is configured, the actor identifier is represented as
a local HMAC-SHA256 digest; otherwise `actor_id_hash` is omitted.

## Live contract verification (phase 4)

After starting Observer and applying its migrations, verify the complete producer to
storage to read-API path from the ManitOS checkout:

```bash
MANITOS_OBSERVER_ENABLED=1 \
MANITOS_OBSERVER_URL=http://127.0.0.1:8000 \
MANITOS_OBSERVER_PROJECT_ID=manitos-smoke \
MANITOS_OBSERVER_ENVIRONMENT=integration \
python scripts/check_observer_integration.py
```

The command uses the same bounded background exporter as a real turn. It sends one
synthetic trace, flushes the delivery queue, reads the trace and spans back through
Observer, and verifies schema version, project, environment, instance, session, turn,
and metadata-only input/output fields. It exits non-zero on rejection, timeout,
contract drift, missing spans, or content leakage. The JSON result contains correlation
metadata and counters only; API keys and actor hashes are never printed.

## Operator correlation (phase 5)

The Observer trace list accepts exact filters for:

- `project_id`
- `environment`
- `service_instance_id`
- `session_id`
- `turn_id`

The dashboard exposes project, environment, and session filters and shows project and
session correlation in the trace table. Trace detail shows project, environment,
service instance, session, turn, and schema version. `actor_id_hash` remains available
to authorized API consumers but is deliberately not rendered in the dashboard.

Recommended first-use sequence:

1. Apply `alembic upgrade head` and start Observer.
2. Run the phase 4 live check with a dedicated smoke project/environment.
3. Open **Traces** and filter by that project.
4. Confirm the runtime-correlation block and the metadata-only spans.
5. Enable the real ManitOS exporter only after the smoke succeeds.

## Durable delivery (phase 6)

Set `MANITOS_OBSERVER_SPOOL_KEY` to enable the local durable queue. Envelopes that
exhaust transient retries are stored in SQLite as AES-GCM ciphertext and retried after
Observer recovers or ManitOS restarts. Idempotency identities are stored only as
SHA-256 hashes. There is no plaintext persistence fallback.

Optional controls include `MANITOS_OBSERVER_SPOOL_PATH`,
`MANITOS_OBSERVER_SPOOL_MAX_ITEMS`, `MANITOS_OBSERVER_SPOOL_RETRY_SEC`,
`MANITOS_OBSERVER_CIRCUIT_FAILURE_THRESHOLD`, and
`MANITOS_OBSERVER_CIRCUIT_RECOVERY_SEC`. The circuit breaker diverts new envelopes to
the spool while Observer is unavailable, keeping network failures out of the turn
path. ManitOS `/readyz` exposes only metadata-only delivery status and counters.

## Quality analytics (phase 7)

`GET /v1/analytics/manitos-quality` accepts `hours`, `project_id`, and optional
`environment` query parameters. It aggregates each signal once per turn and reports
error, degraded, truncated, tool-error, TTS-error, and local-fallback rates; average turn latency
and TTFT; plus model and language distributions. Empty windows return zero-valued
metrics. The Overview dashboard renders the same metadata-only indicators for the
`manitos` project.

## Passive release gate (phase 8)

Phase 8 observes normal ManitOS use without generating prompts or synthetic turns. The
gate samples Observer health, the metadata-only quality summary, and the
`observer_exporter` block from ManitOS `/readyz`. It writes an atomic progress report
that contains only bounded counters, distributions, circuit state, and timestamps.
API keys, actor hashes, endpoints with credentials, prompts, responses, tool arguments,
and artifacts are never copied into the report.

After starting both applications and enabling the exporter, run a 24-hour window:

```bash
cd backend
python -m app.ops.manitos_passive_gate \
  --duration-hours 24 \
  --interval-seconds 60 \
  --minimum-turns 20 \
  --project-id manitos \
  --environment development
```

This command is passive: real turns happen only when a person uses ManitOS. It fails
closed when too few turns are observed or when availability, error, degradation,
truncation, tool, fallback, TTS, latency, spool, circuit, privacy, or durable-delivery
thresholds are violated. Progress and the final decision are written to
`.observer-state/manitos-phase8.json`; the directory is ignored by Git.

For a non-promotional connectivity probe, set `--duration-seconds 0 --minimum-turns 0`.
The default gate still requires durable encrypted delivery. Use
`--allow-volatile-delivery` only for local diagnostics, never for release evidence.

## Database migration

From `backend/`:

```bash
alembic upgrade head
```

Alembic reads `DATABASE_URL` at runtime. The migration is reversible on both SQLite and
PostgreSQL and creates correlation columns plus `ingestion_receipts`.
