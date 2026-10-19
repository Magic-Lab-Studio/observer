# Migration `20261019_parent_ref`: `spans.parent_span_id` without a foreign key

## What changes

| | Before | After |
|---|---|---|
| `spans.parent_span_id → spans.id` | foreign key | plain reference (indexed) |
| `spans.trace_id → traces.id ON DELETE CASCADE` | foreign key | unchanged (now named `fk_spans_trace_id_traces` on SQLite) |
| Indexes `ix_spans_trace_id`, `ix_spans_parent_span_id` | present | present |
| Columns, types, other tables | | unchanged; no row is rewritten |

**Why:** a child span finishes, and is exported, before its parent, and an OpenTelemetry parent
can belong to another service. With SQLite foreign keys enforced, the self-reference rejected
every child that arrived first (HTTP 500, even inside one batch), and the OTLP receiver dropped
parents it had not stored yet. Trace integrity stays with `spans.trace_id`.

On SQLite the table is rebuilt (Alembic batch mode); on PostgreSQL the constraint is dropped in
place.

## Rehearsal results

Measured on copies of a production SQLite database (2,447 traces, 9,757 spans, 1,069
evaluations) and on a synthetic copy ten times larger:

| Check | Result |
|---|---|
| Upgrade, 9,757 spans | 1.59 s including process start |
| Upgrade / downgrade, 97,570 spans (59.5 MB) | 1.19 s / 1.25 s |
| Content after upgrade | every table identical in row count and content hash |
| Integrity | `PRAGMA integrity_check` ok and `foreign_key_check` clean, after upgrade and after downgrade |
| File size (SQLite) | grows by the rebuilt table's free pages (8.9 → 12.4 MB in the rehearsal); SQLite reuses them; `VACUUM` is optional |
| PostgreSQL 16 | full chain from an empty database, and base → head → base → head |
| Reads of historic traces, cascade delete, Manitos re-ingestion (duplicate), generic and OTLP children sent before their parent | all as expected on the migrated copy |

## Rollback

`alembic downgrade 20261018_settings` restores the constraint.

* Right after the upgrade it returns the data unchanged.
* **Lossy case:** a span stored by the new version whose parent never arrived (a remote
  OpenTelemetry parent, an interrupted trace) cannot satisfy the restored constraint; the
  downgrade sets that `parent_span_id` to NULL. Nothing else is lost.
* **Clients:** the Python SDK from this release sends children before parents. A backend rolled
  back to the old schema with SQLite foreign keys on answers 500 to those children, so roll such
  clients back too, or keep them on the previous SDK until the backend is settled. Integrations
  that send whole traces at once (the Manitos envelope) are not affected.
* A downgrade keeps the data written after the upgrade. For an exact return to the
  pre-migration state, restore the backup taken before it.

## Deployment checklist

1. Back up the database: SQLite with the online backup API (`sqlite3 db ".backup copy.db"`)
   while the service runs, then `PRAGMA integrity_check` and a checksum of the copy;
   PostgreSQL with `pg_dump`.
2. Install the release (the backend now needs FastAPI ≥ 0.121).
3. `alembic upgrade head`.
4. Restart the backend and the dashboard; check `/health`, a trace list and one trace detail.
5. Optionally run `scripts/agent_scenarios.py` against the instance.
6. Upgrade instrumented applications to the new SDK only after the backend runs the new schema.
