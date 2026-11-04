"""PR4, PR6, T25: the nightly encrypted backup, and the purge that runs under its heartbeat.

The script runs with its outside commands replaced by stubs (ops_scripts.py), so these
tests check what it runs and in what order, with no database, bucket or network.
"""

import re
from pathlib import Path

import pytest
from ops_scripts import HEALTHCHECK_URL, copy_ops_folder, run_with_stubs

REPO_ROOT = Path(__file__).parent.parent
BACKUP_SCRIPT = (REPO_ROOT / "ops" / "backup" / "backup_tracker.sh").read_text(encoding="utf-8")


def test_nothing_readable_touches_the_disk_during_a_backup() -> None:
    assert '| age -r "$AGE_RECIPIENT" | rclone rcat' in BACKUP_SCRIPT
    assert not re.search(r"--file[= ]", BACKUP_SCRIPT)
    assert "set -euo pipefail" in BACKUP_SCRIPT


def test_the_backup_key_is_configuration_not_code() -> None:
    assert not re.search(r"age1[0-9a-z]{20,}", BACKUP_SCRIPT)
    assert 'AGE_RECIPIENT="$(require AGE_RECIPIENT)"' in BACKUP_SCRIPT


def test_the_backup_keeps_the_period_the_notice_states() -> None:
    from tracker.channel.replies import CONSENT_NOTICE

    assert "KEEP=14" in BACKUP_SCRIPT
    assert "paling lama 14 hari" in CONSENT_NOTICE


def test_the_backup_uploads_prunes_then_reports_success(tmp_path: Path) -> None:
    stubs = {"docker": "", "pg_dump": "", "age": "", "rclone": "", "curl": ""}
    calls = run_with_stubs(copy_ops_folder(tmp_path) / "backup" / "backup_tracker.sh", tmp_path, stubs)
    assert calls[0].endswith(f"{HEALTHCHECK_URL}/start")
    assert calls[-1].endswith(HEALTHCHECK_URL)
    assert any(call.startswith("rclone rcat :s3:example-bucket/tracker/tracker-") for call in calls)
    assert any(call.startswith("rclone lsf :s3:example-bucket/tracker ") for call in calls)


def test_the_nightly_run_purges_expired_records_after_the_backup(tmp_path: Path) -> None:
    """Retention runs under the backup's heartbeat, so a purge that never runs alerts (R16)."""
    stubs = {"docker": "", "pg_dump": "", "age": "", "rclone": "", "curl": ""}
    calls = run_with_stubs(copy_ops_folder(tmp_path) / "backup" / "backup_tracker.sh", tmp_path, stubs)
    purge = next(index for index, call in enumerate(calls) if call.endswith("run --rm cli purge"))
    upload = next(index for index, call in enumerate(calls) if call.startswith("rclone rcat"))
    assert upload < purge
    assert calls[-1].endswith(HEALTHCHECK_URL)


def test_a_failed_purge_still_keeps_the_backup_and_reports_failure(tmp_path: Path) -> None:
    stubs = {"docker": 'case "$*" in *purge*) exit 1 ;; esac', "pg_dump": "", "age": "", "rclone": "", "curl": ""}
    calls = run_with_stubs(copy_ops_folder(tmp_path) / "backup" / "backup_tracker.sh", tmp_path, stubs)
    assert any(call.startswith("rclone rcat") for call in calls)
    assert calls[-1].endswith(f"{HEALTHCHECK_URL}/fail")


def test_the_owner_password_never_reaches_pg_dumps_command_line(tmp_path: Path) -> None:
    """Any local user can read a command line; the password comes from a passfile (L1)."""
    stubs = {
        "docker": "",
        "pg_dump": 'echo "PGPASSFILE=$PGPASSFILE" >> "$CALL_LOG"',
        "age": "",
        "rclone": "",
        "curl": "",
    }
    calls = run_with_stubs(copy_ops_folder(tmp_path) / "backup" / "backup_tracker.sh", tmp_path, stubs)
    assert "pg_dump postgresql://owner@db.invalid:5432/tracker --format=custom" in calls
    assert "PGPASSFILE=/example/pgpass" in calls


@pytest.mark.parametrize(
    "url", ["postgresql://owner:example@db.invalid/tracker", "postgresql://owner@db.invalid/tracker?password=example"]
)
def test_a_database_url_holding_a_password_is_refused(tmp_path: Path, url: str) -> None:
    stubs = {"docker": "", "pg_dump": "", "age": "", "rclone": "", "curl": ""}
    settings_changes = {"BACKUP_DATABASE_URL": url}
    calls = run_with_stubs(
        copy_ops_folder(tmp_path) / "backup" / "backup_tracker.sh", tmp_path, stubs, settings_changes
    )
    assert not any(call.startswith("pg_dump") for call in calls)
    assert calls[-1].endswith(f"{HEALTHCHECK_URL}/fail")


def test_a_failed_dump_reports_failure(tmp_path: Path) -> None:
    stubs = {"docker": "", "pg_dump": "exit 1", "age": "", "rclone": "", "curl": ""}
    calls = run_with_stubs(copy_ops_folder(tmp_path) / "backup" / "backup_tracker.sh", tmp_path, stubs)
    assert calls[-1].endswith(f"{HEALTHCHECK_URL}/fail")
    assert not any(call.startswith("rclone lsf") for call in calls)


def test_whatsapps_session_files_are_not_backed_up() -> None:
    assert ".sessions" not in BACKUP_SCRIPT.replace("WAHA's session files", "")
