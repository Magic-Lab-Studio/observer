# First-time setup

Observer can run independently of any specific agent runtime. A Python,
TypeScript, or custom application sends generic trace spans to Observer; the
backend stores them and exposes analytics and evaluation APIs.

```text
Application -> POST /v1/traces/batch -> Observer API -> SQLite/PostgreSQL
                                                   -> analytics/evaluations
```

The ManitOS ingestion route is an optional compatibility integration. A general
application does not need it.

## 1. Start Observer locally

Clone the repository and use the protected default branch. For a reproducible
deployment, pin the commit or the next release that contains this guide:

```bash
git clone https://github.com/Magic-Lab-Studio/observer.git
cd observer
git checkout main
docker compose up -d
```

Wait for the backend and verify it:

```bash
curl http://localhost:8000/health
```

The default Compose configuration is intended for local evaluation. It starts
the API on port `8000`, the dashboard container on port `5173`, and PostgreSQL
on port `5432`.

For a lightweight API-only installation, use SQLite:

```bash
python -m venv .venv
python -m pip install -e "./backend[sqlite]"
cd backend
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 2. Send the first trace

The generic ingestion endpoint accepts batches of spans and groups them by
`trace_id`. Timestamps are Unix seconds.

```bash
curl -X POST http://localhost:8000/v1/traces/batch \
  -H "Content-Type: application/json" \
  -d '{
    "spans": [{
      "trace_id": "demo-support-session",
      "name": "answer-question",
      "span_type": "llm",
      "start_time": 1785600000.0,
      "end_time": 1785600001.2,
      "status": "ok",
      "tokens_input": 240,
      "tokens_output": 90,
      "cost_usd": 0.0012,
      "attributes": {
        "provider": "example-provider",
        "model": "example-model",
        "environment": "development"
      }
    }]
  }'
```

Confirm that Observer stored it:

```bash
curl http://localhost:8000/v1/traces
```

If `OBSERVATORY_API_KEY` is configured, include
`Authorization: Bearer <key>` in both requests.

## 3. Connect an application

Observer accepts direct HTTP ingestion, a Python SDK, or a TypeScript SDK. The
canonical package distribution names are:

| Component | Distribution | Stable code interface |
| --- | --- | --- |
| Python SDK | `magic-lab-observer` | `import llm_observatory` |
| TypeScript SDK | `@magic-lab-studio/observer` | Import from the scoped package |
| Python CLI | `magic-lab-observer-cli` | `llm-observatory` command |
| Python backend | `magic-lab-observer-backend` | `app` package and existing entry points |

Until these distributions are published by Magic Lab Studio, install them from
the checked-out repository instead of installing similarly named unscoped
packages from a public registry:

```bash
python -m pip install "./sdk/python[openai,anthropic,langchain]"
python -m pip install ./cli
cd sdk/typescript
npm ci
npm run build
```

Do not use `pip install llm-observatory` or `npm install llm-observatory` as a
substitute: those unscoped registry names are not the distribution identity of
this repository.

The first registry publication under the canonical names is planned for
Observer `0.1.1`, so it can correspond to a source tag newer than the existing
`v0.1.0` release.

For custom runtimes, direct HTTP is the smallest stable contract. Send completed
spans to `/v1/traces/batch`; use a consistent `trace_id` to connect model, tool,
retrieval, and agent operations in one trace.

## 4. Choose a privacy mode

Generic spans may contain `input` and `output` objects. Auto-instrumentation can
therefore capture prompts or model responses. For metadata-only operation, omit
those fields and send only timing, status, token, cost, model, provider, and
other non-sensitive attributes.

Never send credentials, personal data, secret-bearing URLs, or raw content that
your retention policy does not permit.

## 5. Prepare a shared deployment

Before exposing Observer outside a development machine:

- Replace the example database credentials and keep the database private.
- Set `OBSERVATORY_API_KEY` and pass it as a Bearer token from exporters.
- Terminate TLS at a trusted reverse proxy or ingress.
- Restrict `CORS_ORIGINS` to the actual dashboard and operator origins.
- Put the dashboard and API behind an authentication-aware gateway.
- Configure backups, retention, and deletion appropriate to the captured data.
- Validate SQLite or PostgreSQL migrations during every upgrade.

The generic integration gate report
`observer.integration.passive_gate.v2` is optional. It can validate a custom
runtime integration without adopting the ManitOS-specific ingestion contract.

## Package publication ownership

PyPI does not support organization scopes, so the Python distributions use the
`magic-lab-` prefix. npm supports scopes, so the TypeScript SDK uses the
`@magic-lab-studio` scope. Publishing should use registry trusted publishing
from a protected GitHub environment rather than long-lived API tokens.

Before the first registry release, organization maintainers must:

1. Create or verify the `magic-lab-studio` npm organization and grant the
   release maintainers access.
2. Reserve the three Python distribution names with an initial release from
   this repository.
3. Configure PyPI and npm trusted publishing for this GitHub repository.
4. Publish matching versions and verify the installed package metadata and
   provenance.
5. Update this guide only after registry installation has been tested from a
   clean environment.
