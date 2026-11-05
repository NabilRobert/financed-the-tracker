"""Runs an ops/ script with its outside commands (docker, rclone, curl...) replaced by stubs.

Each stub logs its arguments, in order, then runs the shell the test gives it (print a
listing, fail, keep what it's sent), so a test can check what the script ran, in what
order, and what it skipped after a failure. Nothing reaches a network.
"""

import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
HEALTHCHECK_URL = "https://heartbeat.invalid/check"
FAKE_SETTINGS = {
    "S3_BUCKET": "example-bucket",
    "S3_REGION": "example-region",
    "AWS_ACCESS_KEY_ID": "example-id",
    "AWS_SECRET_ACCESS_KEY": "example-secret",
    "AGE_RECIPIENT": "example-recipient",
    "BACKUP_DATABASE_URL": "postgresql://owner@db.invalid:5432/tracker",
    "BACKUP_PGPASSFILE": "/example/pgpass",
    "BACKUP_HEALTHCHECK_URL": HEALTHCHECK_URL,
    "DELETIONS_HEALTHCHECK_URL": HEALTHCHECK_URL,
}


def _write_stub(folder: Path, name: str, behaviour: str) -> None:
    stub = folder / name
    stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "$CALL_LOG"\n{behaviour}\n', encoding="utf-8")
    stub.chmod(0o755)


def copy_ops_folder(tmp_path: Path) -> Path:
    """A copy of ops/ to run from, so a script's files land in tmp_path, not the repo."""
    ops = tmp_path / "ops"
    shutil.copytree(REPO_ROOT / "ops" / "backup", ops / "backup")
    shutil.copy(REPO_ROOT / "ops" / "compose.yaml", ops / "compose.yaml")
    return ops


def run_with_stubs(
    script: Path, tmp_path: Path, stubs_by_name: dict[str, str], settings_changes: dict[str, str] | None = None
) -> list[str]:
    """Runs the script with a stub for each named command, which then runs the given shell
    ("" to succeed), and the fake settings with any changes. Returns the calls, one per line."""
    calls, _ = run_with_stubs_and_result(script, tmp_path, stubs_by_name, settings_changes)
    return calls


def run_with_stubs_and_result(
    script: Path,
    tmp_path: Path,
    stubs_by_name: dict[str, str],
    settings_changes: dict[str, str] | None = None,
    arguments: tuple[str, ...] = (),
) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    """As run_with_stubs, with arguments for the script, and its exit code and output too."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, behaviour in stubs_by_name.items():
        _write_stub(stubs, name, behaviour)
    settings = tmp_path / "backup.env"
    values = FAKE_SETTINGS | (settings_changes or {})
    settings.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    call_log = tmp_path / "calls.log"
    call_log.touch()
    environment = {
        **os.environ,
        "PATH": f"{stubs}:{os.environ['PATH']}",
        "TRACKER_BACKUP_ENV": str(settings),
        "CALL_LOG": str(call_log),
    }
    bash = shutil.which("bash")
    assert bash is not None
    result = subprocess.run(  # noqa: S603 - our own script.
        [bash, str(script), *arguments], env=environment, check=False, capture_output=True, text=True
    )
    return call_log.read_text(encoding="utf-8").splitlines(), result


# What `deletions-run` prints on a quiet run, which export_deletions.sh needs to see (R12).
QUIET_DELETIONS_RUN = 'case "$*" in *deletions-run*) echo "deletions_run deleted=0" ;; esac'


def run_export_deletions(
    tmp_path: Path, *, on_server: str, in_bucket: str | None, docker: str = QUIET_DELETIONS_RUN
) -> tuple[list[str], str]:
    """Runs export_deletions.sh with the list on the server and in the bucket as given
    (None: the bucket has no copy). Returns the calls and what was uploaded ("" if nothing)."""
    ops = copy_ops_folder(tmp_path)
    (ops / "deletions").mkdir()
    (ops / "deletions" / "deleted-ids.txt").write_text(on_server, encoding="utf-8")
    uploaded = tmp_path / "uploaded.txt"
    listing = "deleted-ids.txt" if in_bucket is not None else ""
    rclone = (
        f'case "$1" in lsf) echo "{listing}" ;; cat) printf "{in_bucket or ""}" ;; rcat) cat > "{uploaded}" ;; esac'
    )
    script = ops / "backup" / "export_deletions.sh"
    calls = run_with_stubs(script, tmp_path, {"docker": docker, "rclone": rclone, "curl": ""})
    return calls, uploaded.read_text(encoding="utf-8") if uploaded.exists() else ""
