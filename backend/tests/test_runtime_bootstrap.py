"""Recovery failures must never discard the operator's existing environment."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "scripts" / "bootstrap_runtime.sh"


def test_bootstrap_restores_backup_after_install_failure(tmp_path):
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = /missing/python\n")
    (venv / "sentinel").write_text("original environment")
    tools = tmp_path / "bin"
    tools.mkdir()
    uv = tools / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = venv ]; then\n'
        '  target="${@: -1}"\n'
        '  mkdir -p "$target"\n'
        '  touch "$target/partial"\n'
        "  exit 0\n"
        "fi\n"
        "exit 42\n"
    )
    uv.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "TMPDIR": str(ROOT / ".runtime" / "test-tmp"),
    }
    result = subprocess.run(
        ["bash", str(BOOTSTRAP), "repair", "--venv", str(venv)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 42, result.stderr
    assert (venv / "sentinel").read_text() == "original environment"
    failed = list(tmp_path.glob("venv.failed-*"))
    assert len(failed) == 1
    assert (failed[0] / "partial").exists()


def test_bootstrap_refuses_unrelated_directory(tmp_path):
    target = tmp_path / "user-files"
    target.mkdir()
    (target / "sentinel").write_text("keep")
    result = subprocess.run(
        ["bash", str(BOOTSTRAP), "repair", "--venv", str(target)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "non-venv" in result.stderr
    assert (target / "sentinel").read_text() == "keep"
