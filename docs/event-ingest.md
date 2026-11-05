# Ingest the events a system already emits

Many agents already write structured JSON events, such as a job id, an event name, a timestamp
and started/completed pairs. They also call their models through their own layer, so neither the
SDK instrumentors nor OTLP see them. Observer can read those events as they are. A YAML mapping
says how events become traces, and nothing in the system has to change. This is roadmap step 11.

```bash
observer-ingest --mapping map.yaml --from events.jsonl --project my-agent --dry-run   # look first
observer-ingest --mapping map.yaml --from events.jsonl --project my-agent             # then send
```

The command posts to `POST /v1/ingest/events`. It is idempotent: span ids, trace ids and keys are
derived from the events, so sending the same file again, for a retry or a backfill, stores
nothing new.

## The mapping

```yaml
version: 1
event: event            # field holding the event name
time:
  field: ts             # field holding the time
  unit: ms              # s | ms | iso (ISO 8601 with a time zone)
trace:
  key: job_id           # events with the same value form one trace
  name: support.job     # optional; by default the root span's name
  session: conversation # optional field: groups traces into a session
spans:
  - start: job.started  # optional; without it the span ends at `end`
    end: job.completed
    name: job           # a literal name, or name_field: <field>
    type: agent         # a literal type, or type_field: <field>
    status_field: status            # ok | success | completed | error | failed
    attributes:                     # a list of field names, or {attribute: field}
      observer.outcome: outcome
  - start: tool.started
    end: tool.completed
    key: call_id        # tells instances of this span apart within a trace
    parent: parent_step # field holding the key of the parent span
    name_field: tool
    type: tool
    attributes: [tool.name]
    content:            # content follows the project's capture mode
      input: args
      output: result
  - end: cache.hit
    name: cache
    duration_field: duration_ms     # an end-only span starts duration_ms before it
```

### Rules

- **The mapping is closed.**
  - An unknown key is an error (422, or exit 2 for the command).
  - An event name may belong to only one span spec.
- **A field the mapping does not name never reaches the database.** Attributes take scalars or
  lists of scalars; objects are skipped, so name their fields one by one.
- **Content goes in `content.input` / `content.output`.**
  - Its fate depends on the capture mode: the project's own setting, else
    `OBSERVATORY_EVENTS_CAPTURE`, else `metadata`.
  - In `metadata` mode content is dropped, and so are attribute keys that look like content.
    See [privacy.md](privacy.md).
- **Ids:**
  - A trace key that is a UUID becomes the trace id. Any other value becomes a UUIDv5 of the
    project and the key.
  - A span instance is identified by its trace and its `key` field, or by its spec when there
    is no `key`.
- **Agent contract:** map fields to `observer.outcome`, `close.kind`, `gate.status` and the other
  keys of [agent-spans.md](agent-spans.md) to get outcomes and the "why" panel. A span type can
  come from a field (`type_field`).
- **Events that do not fit are counted, never fatal.** The report counts:
  - `ignored`: no span spec uses the event name;
  - `no_trace_key`: the event has no value in the `trace.key` field;
  - `bad_time`: the time field could not be read;
  - `not_an_object`: the line is valid JSON but not an object;
  - `unfinished`: a span that started and never ended. It is sent as a partial span, with no end
    time and status `unset`.

## The route

`POST /v1/ingest/events`, with the same API key as the other routes:

```json
{"project_id": "my-agent", "environment": "production", "mapping": "<YAML text or an object>",
 "events": [{"event": "job.started", "job_id": "j-1", "ts": 1790000000000}], "dry_run": false}
```

- **Limits:** at most 20,000 events per request, and `RATE_LIMIT_EVENTS` requests (default
  `120/minute`).
- **Grouping:** events are grouped by trace inside one request, so a span's start and end must
  travel together. `observer-ingest` batches whole traces for that reason (`--batch-size`).
- **Answer:**
  - `accepted`, `duplicates`, `rejected` and `errors`, counted in traces;
  - the event `report`;
  - with `dry_run`, the traces it would create.

## The command

`observer-ingest` is installed with the backend:

| Option | Meaning |
|---|---|
| `--mapping` | the YAML mapping |
| `--from` | a JSONL file, or `-` for standard input; blank lines are skipped and lines that are not JSON are counted in `bad_lines` |
| `--project`, `--environment` | where the traces go |
| `--url`, `--api-key` | default `OBSERVER_URL` / `OBSERVATORY_API_KEY` |
| `--batch-size` | events per request (default 5000); a trace is never split |
| `--dry-run` | convert locally and print the traces; nothing is sent |

Exit status:
- `0`: every trace was stored, or the dry run read the mapping;
- `1`: some trace was rejected;
- `2`: it could not run (bad mapping or file, or Observer unreachable or refusing).

**Sources:** only JSONL files are built. Loki and Azure Log Analytics would implement the same
`EventSource` interface (`app/ops/event_ingest.py`); they are not built or verified.

## Checked by

- **Test bed:** `examples/orchestrator` sends its episodes as the orchestrator's own events
  (project `bed-events`).
  - Each step is a `step.started`/`step.completed` pair, with a field the mapping does not name.
  - It runs the same checks as the batch and OTLP renderings: outcomes, structure, and the
    unmapped field absent.
  - Sending the same events a second time stores nothing.
- **Tests:** `backend/tests/test_event_ingest.py` and `backend/tests/test_bed_e2e.py`.
