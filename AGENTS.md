# AGENTS.md

Context for coding agents (including Google Jules) working in this repository.

## Identity

- Repository: `Magic-Lab-Studio/observer`.
- Product: **Observer** (an LLM observability and evaluation platform). The long
  name "LLM Observatory" and the package name `magic-lab-observer` refer to the
  same product. Use "Observer" or "LLM Observatory", never invent other names.
- **ManitOS** is one supported integration lane (agent-runtime telemetry), not
  the name of the product. The repository history also uses "ManitOS" as an
  internal codename; prefer "Observer" in new public-facing text.
- License: Apache 2.0. All content is public-facing; do not add internal-only
  notes, credentials, or personal data.

## Layout

- `backend/` — FastAPI backend, package `magic-lab-observer-backend`.
  - `app/api/` — route handlers (`traces`, `evaluations`, `analytics`, `manitos_ingest`).
  - `app/evaluators/` — rubric/LMM-as-Judge engine.
  - `app/models/` — SQLAlchemy models.
  - `app/ops/` — e.g. `manitos_passive_gate.py`.
  - `alembic/` — database migrations (SQLite + PostgreSQL).
- `sdk/python/` — Python SDK, package `magic-lab-observer`, import name `llm_observatory`.
- `sdk/typescript/` — TypeScript SDK, package `@magic-lab-studio/observer`.
- `cli/` — CLI package `magic-lab-observer-cli`, binary `llm-observatory`.
- `dashboard/` — React + Vite dashboard.
- `docs/` — `first-time-setup.md`, `manitos-integration.md`, `releasing.md`.
- `.github/workflows/` — `ci.yml`, `dependency-audit.yml`, `publish.yml`.
- `.observer-state/` — runtime logs/state, not documentation. Do not edit.

## Canonical commands

Run these from the repository root unless noted.

- Lint: `ruff check backend cli sdk/python`
- Backend tests: `cd backend && python -m pytest tests -q`
- Python SDK tests: `cd sdk/python && python -m pytest tests -q`
- CLI tests: `cd cli && python -m pytest tests -q`
- TypeScript SDK: `cd sdk/typescript && npm ci && npx tsc --noEmit && npm run test:run`
- Dashboard: `cd dashboard && npm ci && npm run build`
- Migrations smoke (SQLite and PostgreSQL): see `ci.yml` jobs
  `sqlite-migrations` and `postgres-migrations`.

Do not report test/lint results you have not actually produced by running the
commands above. Prefer to read GitHub CI status for the branch instead.

## Conventions

- Python 3.10+, ruff-clean. No code comments unless they add non-obvious context.
- Never add secrets, API keys, or `.env` values to tracked files.
- SDK/runtime contracts are versioned and additive; do not break
  `POST /v1/ingest/manitos/traces` or other published endpoints.
- Release flow is documented in `docs/releasing.md`: four packages, one
  `v<version>` tag, trusted publishing only.

## Review output contract

When the task is a review, produce this structure in the PR description and in
any review file. Do not report findings without evidence (`path:line`).

```text
## Summary
<what was reviewed, over what window, at which commit>

## Findings
- [SEVERITY: critical|high|medium|low] <issue> — <evidence path:line> — <action>

## Checklist
- [x] CI status checked (gh pr checks / actions)
- [x] lint: ruff check backend cli sdk/python
- [x] tests: backend / sdk/python / cli (actual counts)
- [x] migrations smoke
- [x] docs/terminology consistent (Observer, package names)

## Verdict
PASS | BLOCK — <one-sentence justification>
```

Rules:

- Only report tests you actually ran; otherwise state "not run" and rely on CI.
- Every finding needs evidence; a review with "none" is a verdict, not a finding.
- Do not create a PR for a purely read-only review with no change unless the
  task explicitly asks for a PR. Prefer a comment or issue.
