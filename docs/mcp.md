# Read-only MCP server

`observer-mcp` lets an assistant (Claude Code, Codex, any MCP client) investigate an Observer
instance without screenshots or copy-paste. It speaks MCP over stdio and reads from a running
Observer through its HTTP API.

```bash
pip install -e "./backend[mcp]"      # the `mcp` package is an optional extra
claude mcp add observer \
  -e OBSERVER_URL=http://127.0.0.1:8100 -e OBSERVATORY_API_KEY=<key-if-set> \
  -- /path/to/backend/.venv/bin/observer-mcp
```

Any other client: run `observer-mcp` (or `python -m app.ops.mcp_server`) as a stdio server with
`OBSERVER_URL` (default `http://127.0.0.1:8100`) and, when the backend requires one,
`OBSERVATORY_API_KEY`.

## What it can do

Every tool is read-only (declared with `readOnlyHint`), and there is no tool that writes.

| Tool | Answers |
|---|---|
| `observer_overview` | Turn quality over the last N hours and the open alerts |
| `list_traces` | Recent traces (filter by status, environment, project, search) |
| `list_failed_traces` | Traces with status `error` plus turns whose health score fell below a threshold, each with the reason |
| `get_trace` | One trace: spans (name, type, status, duration, attributes), tools called, the provider chain, answering model, scores |
| `list_alerts` | Alert events, open or resolved |
| `list_datasets`, `get_dataset` | Test-case datasets and their cases |
| `list_runs`, `compare_runs` | Runs per agent version and the case-by-case difference between two |
| `metrics_schema`, `query_metrics` | Flexible aggregates (view, measures, dimensions, filters) |

A good session: `observer_overview` → `list_failed_traces` → `get_trace` on one → `compare_runs`
to see whether a fix helped.

## Privacy and safety

* **No content by default.** Trace and span `input`/`output` are never returned. Attributes and
  trace metadata are **allow-listed**: only named structural keys (model, language, intent, status,
  provider chain, latency, `gen_ai.usage.*`, tool and agent names…) pass, with scalar values cut to
  200 characters on one line; everything else is withheld because senders can put anything there
  (keys, paths, private text, even instructions aimed at the assistant). Dataset notes, descriptions
  and the phrases a reply must contain are shown only as counts. Span and trace names are free text:
  they are shortened and stripped of control characters, nothing more. Set `OBSERVER_MCP_CONTENT=1`
  on the server only if you accept that the assistant sees stored prompts, replies and every
  attribute.
* **Read-only by construction.** The server can issue `GET` requests and the metrics query
  (`POST /v1/analytics/query`, which computes and writes nothing); the paths are fixed in code and a
  test fails if any other method or a write path appears.
* **Local transport.** stdio only: nothing listens on a port. It still talks to Observer over HTTP,
  so the usual API key applies.
* Lists are capped (100 rows, 200 spans per trace).
