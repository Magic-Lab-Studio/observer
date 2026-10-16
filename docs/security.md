# Security notes for deployments

Observer is built for a developer's own machine. Treat it that way until you have put a boundary
in front of it.

## Defaults

* **Network:** `docker-compose.yml` and `podman-compose.yml` publish the backend (8000) and the
  dashboard (5173) on `127.0.0.1` only (`OBSERVER_BIND`, default `127.0.0.1`). PostgreSQL is **not**
  published at all. The preview/dev dashboard servers listen on `127.0.0.1` too
  (`OBSERVER_DASHBOARD_HOST`).
* **Database password:** the compose files have no default password; set `POSTGRES_PASSWORD` before
  `up` (it goes into a URL, so use URL-safe characters).
* **No authentication unless you set `OBSERVATORY_API_KEY`.** The backend logs a warning at start
  when it is empty. Anyone who can reach an unauthenticated backend can read, change and delete
  data.

## Checked (2026-10-16)

* The podman stack was brought up for real (`podman-compose`, rootless podman 5.7): the backend
  migrated PostgreSQL to head, answered `/health`, the dashboard proxied the API, only
  `127.0.0.1` ports were listening and PostgreSQL had none. Without `POSTGRES_PASSWORD` it refuses
  to start. This found that the Containerfiles and compose files used short image names, which
  podman cannot resolve without a configured registry; they are now `docker.io/library/...`.
* `pip-audit` (backend) and `npm audit --omit=dev` (dashboard) reported no known vulnerabilities on
  that date. Repeat them; this is a snapshot.
* The SDK spool (`spool_dir`) stores spans unencrypted with mode 0600; keep it on a private disk.

## Before exposing it on a network

1. Set `OBSERVATORY_API_KEY` (clients send `Authorization: Bearer <key>`).
2. Only then set `OBSERVER_BIND=0.0.0.0`, and put a firewall or a TLS-terminating reverse proxy in
   front. Observer does not terminate TLS.
3. The dashboard's dev/preview proxy adds the backend key to requests it forwards, so whoever can
   open the dashboard gets API access. It only does so for requests from this machine, but a
   reverse proxy on the same host makes every request look local: authenticate in front of the
   dashboard. The container dashboard (nginx) does not add the key, so with a key set it needs your
   own authenticating proxy.
4. Choose a capture mode per project (`docs/privacy.md`) and keep `OBSERVER_MCP_CONTENT` off.

## Limits on untrusted input

Applied before anything is stored; each is an environment variable.

| Limit | Default | Variable |
|---|---|---|
| Request body (any route) | 16 MiB | `OBSERVATORY_MAX_BODY_BYTES` |
| One JSON field (`input`, `output`, `metadata`, `attributes`) | 256 KiB | `OBSERVATORY_MAX_JSON_FIELD_BYTES` |
| JSON nesting depth | 12 | `OBSERVATORY_MAX_JSON_DEPTH` |
| Spans per trace / per batch | 500 / 1000 | `OBSERVATORY_MAX_SPANS_PER_TRACE`, `OBSERVATORY_MAX_SPANS_PER_BATCH` |
| Traces per import | 200 | `OBSERVATORY_MAX_TRACES_PER_IMPORT` |

Rate limits are per client: the API key counts as an identity only when it is the configured one;
otherwise the limiter uses the client IP, so inventing tokens does not buy a fresh budget. Behind a
reverse proxy the IP is the proxy's unless you configure forwarded addresses.

## What is public in this repository

The integration contract and fixtures are meant to be public. Operational notes of a particular
deployment (service names, local paths, hand-off documents) belong in a private repository: deleting
a file here does not remove it from history.
