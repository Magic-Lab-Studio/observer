# Capture privacy

Observer stores what the instrumented app sends. Each **project** can restrict that with a capture
mode, enforced on the server for every ingestion path (Python SDK routes, the Manitos exporter and
OTLP), so the guarantee does not depend on the sender.

| Mode | Stored |
|---|---|
| `metadata` | No span `input`/`output`. Attributes whose key looks like content (prompt, completion, content, message, input, output, query, body, text, payload, url, path, file, argument, email, token, key, secret, authorization, cookie…) are dropped; strings are cut at 200 characters. Standard GenAI attributes that are not content (`gen_ai.usage.*`, model, operation, tool and agent names) are kept. |
| `redacted` | Everything is kept, but emails, phone numbers, Luhn-valid card numbers, `ddd-dd-dddd` ID numbers, bearer tokens, JWTs and common API-key shapes are replaced with `[REDACTED:<kind>]`, and the value of any key that looks secret (`password`, `token`, `api_key`, `authorization`, `cookie`…) is replaced. Also applied to span metadata and trace metadata. |
| `full` | Stored as sent. |

**Without a project mode** each path keeps its historical behaviour, so enabling this changes
nothing by itself: the SDK routes and the Manitos exporter store what they send (Manitos only sends
metadata), OTLP stores `metadata` (`OBSERVATORY_OTLP_CAPTURE`). `OBSERVATORY_CAPTURE_DEFAULT`
(`metadata` | `redacted` | `full`) overrides these defaults for every path.

## Setting a mode and checking it

* Dashboard: **Privacy** page (mode per project, what is stored, "Apply to stored data").
* API: `PUT /v1/privacy/projects/{id}` `{"capture_mode": "redacted"}`; `DELETE` returns to the
  defaults; `GET /v1/privacy/projects` lists projects with a report computed from the latest 2000
  spans of each: spans with an input, with an output, with content-like attributes, with sensitive
  patterns.
* A mode only affects data arriving **after** it is set. `POST /v1/privacy/projects/{id}/enforce`
  with `{"confirm": true}` re-applies the mode to what is already stored. It is **irreversible**
  (inputs/outputs removed for `metadata`; matches replaced for `redacted`).

## Limits

* Redaction is pattern-based. It does **not** detect names, addresses, free-form secrets or
  anything that does not match the patterns above; `metadata` is the only mode that stores no text.
* The attribute filter looks at key names, not values.
* SDK traces posted without a `project_id` have no project to configure; they follow the path
  default or `OBSERVATORY_CAPTURE_DEFAULT`.
* The MCP server (`docs/mcp.md`) is separate: it never returns input/output regardless of mode.
