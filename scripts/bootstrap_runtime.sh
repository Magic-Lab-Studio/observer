#!/usr/bin/env bash
# Local Linux deployment; preserves previous and failed environments for inspection.
set -eEuo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
venv="$root/backend/.venv"
version="$(<"$root/backend/.python-version")"
command="${1:-check}"
if [ "$#" -gt 0 ]; then shift; fi
replace=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --venv) venv="${2:?--venv requires a path}"; shift 2 ;;
    --replace) replace=1; shift ;;
    *) echo "Usage: bash scripts/bootstrap_runtime.sh check|repair [--venv PATH] [--replace]" >&2; exit 2 ;;
  esac
done
case "$command" in check|repair) ;; *) echo "Expected check or repair" >&2; exit 2 ;; esac

healthy() {
  "$venv/bin/python" -I -c 'import sys; assert ".".join(map(str, sys.version_info[:3])) == sys.argv[1]' "$version" 2>/dev/null
}
if [ "$command" = check ]; then
  if healthy; then echo "Python $version: OK ($venv)"; else echo "Python $version: missing or broken ($venv)"; exit 1; fi
  exit 0
fi
mkdir -p -- "$root/.runtime"
exec 9>"$root/.runtime/bootstrap.lock"
flock -n 9 || { echo "Another bootstrap is running" >&2; exit 1; }
if healthy && [ "$replace" = 0 ]; then echo "Interpreter already healthy; use --replace to rebuild dependencies"; exit 0; fi
if [ -L "$venv" ]; then echo "Refusing a symlinked venv directory" >&2; exit 1; fi
venv="$(realpath -m -- "$venv")"
case "$root/" in "$venv/"*) echo "Refusing a project ancestor as venv" >&2; exit 1 ;; esac
if [ -e "$venv" ] && [ ! -f "$venv/pyvenv.cfg" ]; then echo "Refusing a non-venv directory" >&2; exit 1; fi
if [ "$venv" = "$root/backend/.venv" ] && command -v systemctl >/dev/null && systemctl --user is-active --quiet observer-backend; then
  echo "Stop observer-backend with systemctl --user stop before replacing its venv" >&2
  exit 1
fi
command -v uv >/dev/null || { echo "uv is required" >&2; exit 1; }
export TMPDIR="${TMPDIR:-$root/.runtime/tmp}"
TMPDIR="$(realpath -m -- "$TMPDIR")"
case "$TMPDIR/" in /tmp/*|/var/tmp/*) echo "TMPDIR must be on a persistent data disk" >&2; exit 1 ;; esac
export UV_CACHE_DIR="${UV_CACHE_DIR:-$TMPDIR/uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$root/.runtime/python}"
mkdir -p -- "$TMPDIR" "$root/.runtime"
backup="$venv.backup-$(date -u +%Y%m%dT%H%M%S)-$$"
failed="$venv.failed-$(date -u +%Y%m%dT%H%M%S)-$$"
moved=0
if [ -e "$venv" ]; then mv -- "$venv" "$backup"; moved=1; fi
rollback() {
  local result=$?
  trap - ERR INT TERM
  if [ -e "$venv" ]; then mv -- "$venv" "$failed"; echo "Incomplete environment preserved: $failed" >&2; fi
  if [ "$moved" = 1 ]; then mv -- "$backup" "$venv"; echo "Previous environment restored" >&2; fi
  if [ "$result" = 0 ]; then result=1; fi
  exit "$result"
}
trap rollback ERR INT TERM
uv venv --managed-python --python "$version" "$venv"
uv pip install --python "$venv/bin/python" --require-hashes -r "$root/requirements-py312.txt"
uv pip install --python "$venv/bin/python" --no-deps -e "$root/backend" -e "$root/sdk/python" -e "$root/cli"
uv pip check --python "$venv/bin/python"
healthy
"$venv/bin/python" -c 'import app.main, llm_observatory, cli.main'
trap - ERR INT TERM
echo "Ready: $venv (Python $version)"
if [ "$moved" = 1 ]; then echo "Previous environment preserved: $backup"; fi
