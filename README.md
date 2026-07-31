# LLM Observatory

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CI](https://github.com/Magic-Lab-Studio/observer/actions/workflows/ci.yml/badge.svg)](https://github.com/Magic-Lab-Studio/observer/actions/workflows/ci.yml)

Open-source observability, evaluation, and telemetry platform for LLM and
agentic systems.

## Features

- **Multi-framework tracing** — Auto-instrument OpenAI, Anthropic, LangChain
- **Agent runtime integration** — Versioned adapter for metadata-only telemetry ingestion from compatible agent runtimes (for example, ManitOS)
- **Cost tracking** — Per-model pricing for 20+ models (GPT-4o, Claude 4, Gemini 2.0, etc.)
- **Evaluation engine** — LLM-as-Judge with YAML rubric, rule-based metrics, human feedback
- **Real-time dashboard** — Live trace streaming via WebSocket, time-range filtering
- **CLI tools** — Query, export, import, and evaluate traces from the terminal
- **SQLite & PostgreSQL** — Local dev with SQLite, production with PostgreSQL

## Quick Start

### Docker Compose (recommended)

```bash
git clone https://github.com/Magic-Lab-Studio/observer.git
cd observer
docker compose up -d
```

| Service    | URL                      |
|------------|--------------------------|
| Backend    | http://localhost:8000     |
| Dashboard  | http://localhost:5173     |
| PostgreSQL | localhost:5432           |

### Podman Compose

```bash
podman-compose up -d
```

### Manual Setup

**1. Start PostgreSQL:**

```bash
podman run -d --name postgres -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=observatory \
  postgres:16-alpine
```

**2. Backend:**

```bash
cd backend
pip install -e ".[dev,sqlite]"
alembic upgrade head
uvicorn app.main:app --reload
```

**3. Dashboard:**

```bash
cd dashboard
npm install
npm run dev
```

## Project Structure

```
observer/
├── backend/                  # FastAPI backend + evaluation engine
│   ├── app/
│   │   ├── api/              # Route handlers (traces, evaluations, analytics)
│   │   ├── evaluators/       # LLM-as-Judge, rubric engine (YAML/JSON)
│   │   ├── models/           # SQLAlchemy models
│   │   └── main.py           # App entrypoint, health check
│   └── alembic/              # Database migrations
├── dashboard/                # React + Vite + Tailwind
│   └── src/
│       ├── pages/            # Overview, Traces, TraceDetail, Evaluations
│       ├── components/       # TraceWaterfall, ErrorBoundary, shared
│       ├── api.ts            # Centralized API client
│       └── types.ts          # Shared TypeScript interfaces
├── sdk/
│   ├── python/               # llm-observatory PyPI package
│   └── typescript/           # llm-observatory npm package
├── cli/                      # llm-observatory CLI
├── docker-compose.yml
├── podman-compose.yml
└── .github/workflows/ci.yml
```

## Backend API

Base URL: `http://localhost:8000`

| Method   | Endpoint                          | Description                      |
|----------|-----------------------------------|----------------------------------|
| `GET`    | `/health`                         | Health check (db status, version)|
| `POST`   | `/v1/traces/batch`                | Ingest traces                    |
| `POST`   | `/v1/ingest/manitos/traces`       | Versioned metadata-only integration ingestion|
| `GET`    | `/v1/traces`                      | List traces (paginated)          |
| `GET`    | `/v1/traces/{id}`                 | Get trace detail                 |
| `DELETE` | `/v1/traces/{id}`                 | Delete trace                     |
| `POST`   | `/v1/traces/batch-delete`         | Bulk delete traces               |
| `GET`    | `/v1/traces/{id}/evaluations`     | Evaluations for a trace          |
| `GET`    | `/v1/traces/export`               | Export traces as JSON            |
| `POST`   | `/v1/evaluations`                 | Create evaluation                |
| `POST`   | `/v1/evaluations/run`             | Run evaluator on a trace         |
| `GET`    | `/v1/evaluations/summary`         | Aggregated eval stats            |
| `GET`    | `/v1/analytics/summary`           | Trace analytics                  |
| `GET`    | `/v1/analytics/timeline`          | Trace counts over time           |
| `GET`    | `/v1/analytics/cost-by-model`     | Cost breakdown by model          |
| `GET`    | `/v1/analytics/sessions`          | Unique sessions with stats       |
| `GET`    | `/v1/analytics/manitos-quality`   | Quality and latency aggregates for integration telemetry |

The `/v1/ingest/manitos/traces` endpoint uses the versioned `manitos.telemetry.v1`
envelope, accepts opaque session identifiers, and safely deduplicates exporter
retries. It is one of several supported integration adapters; the wire contract
and limits are documented in
[docs/manitos-integration.md](docs/manitos-integration.md).

### Example: Ingest a trace

```bash
curl -X POST http://localhost:8000/v1/traces/batch \
  -H "Content-Type: application/json" \
  -d '{
    "spans": [{
      "trace_id": "demo-001",
      "name": "gpt-4o-call",
      "span_type": "llm",
      "start_time": "2025-01-01T00:00:00Z",
      "end_time": "2025-01-01T00:00:01Z",
      "status": "ok",
      "tokens_input": 150,
      "tokens_output": 50,
      "cost_usd": 0.000875
    }]
  }'
```

## Python SDK

```bash
pip install llm-observatory
```

```python
from llm_observatory import instrument, trace
from llm_observatory.tracer import Tracer

# Auto-instrument LLM libraries
instrument(openai=True, anthropic=True, langchain=True)

# Or trace functions manually
@trace(name="summarize")
def summarize(text: str) -> str:
    return openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": text}]
    )

# Custom tracer with OTLP exporter
tracer = Tracer(service_name="my-app")
from llm_observatory.exporters.otlp import OTLPExporter
tracer.add_exporter(OTLPExporter(endpoint="http://localhost:8000"))
```

### Supported providers

| Provider   | Auto-instrument | Manual trace |
|------------|-----------------|--------------|
| OpenAI     | Yes             | Yes          |
| Anthropic  | Yes             | Yes          |
| LangChain  | Yes             | Yes          |
| Gemini     | Manual only     | Yes          |

## TypeScript SDK

```bash
npm install llm-observatory
```

```typescript
import { instrument, trace, asyncTrace, Tracer } from "llm-observatory";

// Auto-instrument
instrument({ openai: true, anthropic: true });

// Trace async functions
const result = await asyncTrace("summarize", async (span) => {
  const response = await openai.chat.completions.create({
    model: "gpt-4o",
    messages: [{ role: "user", content: text }],
  });
  span.attributes["model"] = "gpt-4o";
  return response;
});

// Custom tracer with OTLP exporter
const tracer = new Tracer({ serviceName: "my-app" });
import { OTLPExporter } from "llm-observatory/exporters/otlp";
tracer.addExporter(new OTLPExporter({ endpoint: "http://localhost:8000" }));
```

## CLI

```bash
pip install llm-observatory-cli
```

```bash
# List traces from local SQLite
llm-observatory traces --db observatory.db

# Inspect a specific trace and its spans
llm-observatory inspect <trace-id>

# Show summary statistics
llm-observatory stats

# Export traces to JSON
llm-observatory export -o traces.json

# Import traces from JSON
llm-observatory import traces.json

# Dry-run import (preview only)
llm-observatory import traces.json --dry-run

# Run an evaluation
llm-observatory evaluate --trace-id abc123 --evaluator llm_judge

# Check server status
llm-observatory status
```

## Evaluation System

The LLM-as-Judge evaluator uses a YAML rubric with 7 criteria:

| Criterion        | Weight | Scale |
|------------------|--------|-------|
| Relevance        | 1.5    | 0-5   |
| Accuracy         | 1.5    | 0-5   |
| Completeness     | 1.0    | 0-5   |
| Coherence        | 1.0    | 0-5   |
| Conciseness      | 0.8    | 0-5   |
| Safety           | 1.2    | 0-5   |
| Hallucination    | 1.5    | 0-5 (inverted) |

Configure the judge model:

```bash
export OPENAI_API_KEY=sk-...
export OBSERVATORY_JUDGE_MODEL=gpt-4o  # default
```

## Development

### Run tests

```bash
# Backend (106 tests)
cd backend && pytest

# Python SDK (37 tests)
cd sdk/python && pytest

# TypeScript SDK (30 tests)
cd sdk/typescript && npx vitest run

# CLI (63 tests)
cd cli && pytest
```

### Lint & typecheck

```bash
ruff check backend/ sdk/python/ cli/
cd sdk/typescript && npx tsc --noEmit
cd dashboard && npx tsc --noEmit
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          LLM Observatory                            │
├──────────────┬──────────────┬───────────────┬───────────────────────┤
│  Python SDK  │ TypeScript   │    Backend    │     Dashboard         │
│  (pip)       │ SDK (npm)    │   (FastAPI)   │   (React + Vite)      │
│              │              │               │                       │
│  OpenAI      │  OpenAI      │  /v1/traces   │   Overview            │
│  Anthropic   │  Anthropic   │  /v1/evals    │   Traces              │
│  LangChain   │              │  /v1/analytics│   Trace Detail        │
│  OTLP export │  OTLP export │  WebSocket    │   Evaluations         │
└──────┬───────┴──────┬───────┴───────┬───────┴───────────┬───────────┘
       │              │               │                   │
       └──────────────┴───────────────┴───────────────────┘
                                     │
                          ┌──────────┴──────────┐
                          │     PostgreSQL      │
                          │   (or SQLite)       │
                          └─────────────────────┘
```

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.
