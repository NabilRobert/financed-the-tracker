"""Runs an ops/ script with its outside commands (docker, rclone, curl...) replaced by stubs.

Each stub logs its arguments, in order, and exits as told, so a test can check what the
script ran, in what order, and what it skipped after a failure. Nothing reaches a network.
"""

import os
import shutil
import subprocess
from pathlib import Path

HEALTHCHECK_URL = "https://heartbeat.invalid/check"
FAKE_SETTINGS = {
    "S3_BUCKET": "example-bucket",
    "S3_REGION": "example-region",
    "AWS_ACCESS_KEY_ID": "example-id",
    "AWS_SECRET_ACCESS_KEY": "example-secret",
    "AGE_RECIPIENT": "example-recipient",
    "TRACKER_MIGRATOR_DATABASE_URL": "postgresql+psycopg://owner:example@db.invalid/tracker",
    "BACKUP_HEALTHCHECK_URL": HEALTHCHECK_URL,
    "DELETIONS_HEALTHCHECK_URL": HEALTHCHECK_URL,
}


def _write_stub(folder: Path, name: str, exit_code: int) -> None:
    stub = folder / name
    stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "$CALL_LOG"\nexit {exit_code}\n', encoding="utf-8")
    stub.chmod(0o755)


def run_with_stubs(script: Path, tmp_path: Path, stub_exit_codes: dict[str, int]) -> list[str]:
    """Runs the script with a stub for each named command. Returns the calls, one per line."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, exit_code in stub_exit_codes.items():
        _write_stub(stubs, name, exit_code)
    settings = tmp_path / "backup.env"
    settings.write_text("".join(f"{key}={value}\n" for key, value in FAKE_SETTINGS.items()), encoding="utf-8")
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
    subprocess.run([bash, str(script)], env=environment, check=False, capture_output=True)  # noqa: S603 - our own script.
    return call_log.read_text(encoding="utf-8").splitlines()
