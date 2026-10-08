# Handoff Codex: Observer

Date: 2026-10-08. Working branch: `feat/span-scalar-attributes`.
Read [AGENTS.md](../AGENTS.md) and [ManitOS integration](manitos-integration.md).
This handoff documents implementation, verification boundaries and resumption;
it does not change ingestion or authorize runtime activation.

## Changes to review

- `f2c37fb`: dashboard scalar span attributes in the selected-span panel.
- `88443a0`: ingestion regression for uncertain local effects without private payloads.
- `faa187f`: ingestion regression for local operator admission metadata.
- Base `2e957f5` contains UTC parsing and dev/preview proxy regression tests (#34).

`dashboard/src/components/SpanDetail.tsx` is used by `TraceDetail.tsx`.
It renders scalar attributes, including `llm.chain` fields `answered_by`,
`attempt_count`, `attempts_text`, `cloud_out_text` and `turn.end_cause` fields
`end_cause`, `http_status`. False, zero, null, empty values and nested JSON remain
representable; text is escaped and long/multiline values wrap. Attributes precede
input/output. No ingestion schema, migrations or backend implementation changes.
API dates must still use `dashboard/src/dates.ts::parseApiDate`.

The working branch is published for review against protected `main`. Publication
and local deployment are not a merge. All five required checks must pass before
any merge: `python-quality`, `python-test`, `typescript`, `sqlite-migrations`,
`postgres-migrations`. This handoff does not authorize skipping checks.

## Verification already performed

Recorded during the implementation, not rerun by this documentation-only close:

- Dashboard: 13 tests passed, including five new render cases in
  `dashboard/tests/span-detail.test.mjs`; TypeScript/Vite build passed.
- Backend: 131 tests passed.
- Chromium: four selected-span interaction/layout cases across desktop
  1440x1000 and mobile 390x844; both span types rendered with fixture attributes.
- Screenshots were inspected and retained locally on SSD. They are temporary
  evidence, not tracked artifacts; regenerate if unavailable next session.
- Only `observer-dashboard` was restarted after building. The local preview
  served the new build and the backend health check reported `db=ok`.

Fixtures do not establish that every live trace contains these attributes or that
the local-operator workflow is enabled. SDK suites and migrations were not rerun
locally in that delivery; consult the PR checks for their current status.

## Resume and reproduce

```bash
export TMPDIR=/mnt/ssd_linux/tmp
git status --short --branch
git fetch origin
git rev-list --left-right --count HEAD...@{upstream}
gh pr list --head feat/span-scalar-attributes
```

From `dashboard/`, with the existing Node 22 installation on PATH:

```bash
export PATH="$HOME/.local/opt/node22/bin:$PATH"
export TMPDIR=/mnt/ssd_linux/tmp
npm run build
npm test
```

Build first: preview proxy tests consume `dist`. From `backend/`:
`env TMPDIR=/mnt/ssd_linux/tmp .venv/bin/python -m pytest tests -q`.
Use the existing uv Python 3.12 environment. Do not migrate to 3.14 incidentally.

Local-only operational checks:

```bash
systemctl --user is-active observer-backend observer-dashboard
curl --fail --silent http://127.0.0.1:8100/health
```

Preview listens on 127.0.0.1:5173 with `/api` proxy to backend 8100 and `/ws`
to `/ws/live`. After future dashboard edits: build, then restart only its user
unit. Do not kill service PIDs, restart the OS runtime or start duplicate servers.
Never put temporary files in `/tmp` or `/var/tmp`; no external telemetry SDKs.

## Next work and boundaries

1. Check the published PR and remote CI; do not conflate pending checks with a
   failure or green local tests with a successful protected-branch merge.
2. The OS/worker plan next implements recoverable move/trash effects in isolated
   fixtures, followed by receipts, restore and newly approved inverse actions.
3. Then extend metadata-only events/tests as needed, without emitting file paths,
   file content, commands or secrets. No ingestion change is needed for this panel.
4. Close the complete L1 workflow before L2-L7. Observer integration/final acceptance
   is not complete merely because scalar fields render.

In the nested workspace, see the [OS handoff](../../docs/handoff_codex_2026_10_08.md)
and [worker handoff](../../manitos-remote/HANDOFF_CODEX_2026_10_08.md). Those sibling
links require the corresponding checkouts; Observer remains an independent repo.
Do not touch the shared service bus without a backup. Preserve unrelated changes,
stage explicit paths and never force push or delete branches without approval.
The dedicated CI server is not yet available; recurring schedules remain disabled.

The user's Spanish delivery format is saved locally as
`~/.codex/skills/manitos-delivery-report/SKILL.md`: Avance, Pruebas Automaticas,
Lo Siguiente; use colored status indicators and distinguish fixtures from deployment.
