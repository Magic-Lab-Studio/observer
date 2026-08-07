# Contributing to Observer

Observer welcomes focused bug fixes, documentation improvements, adapters, and
observability features. Please open an issue before starting a broad redesign or
breaking a published API contract.

## Development setup

Use Python 3.10 or newer and Node.js 22.12 or newer.

```bash
python -m pip install \
  -e "./backend[dev,sqlite]" \
  -e "./sdk/python[dev]" \
  -e ./cli

cd sdk/typescript
npm ci
cd ../../dashboard
npm ci
```

Copy `.env.example` to `.env` only on your own machine. Never commit `.env`,
credentials, real prompts, model responses, personal data, or operational trace
exports.

## Validation

Run the same checks used by CI:

```bash
ruff check backend cli sdk/python

cd backend && python -m pytest tests -q
cd ../sdk/python && python -m pytest tests -q
cd ../../cli && python -m pytest tests -q

cd ../sdk/typescript
npx tsc --noEmit
npm run test:run

cd ../../dashboard
npm run build
```

If a database migration changes, also verify upgrade symmetry:

```bash
cd backend
alembic upgrade head
alembic downgrade base
alembic upgrade head
```

## Pull requests

- Keep each pull request focused and explain user-visible behavior.
- Add regression tests for behavioral changes.
- Preserve released routes, schema versions, migrations, environment variables,
  and package entry points unless a migration plan has been agreed first.
- Use synthetic fixtures. A realistic fixture must still be invented and must
  not be copied from an actual user session.
- Update public documentation when configuration or privacy behavior changes.

By contributing, you agree that your contribution is licensed under the
repository's Apache 2.0 license.
