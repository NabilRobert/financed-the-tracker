"""L2, T45, T27: the server can't delete its own backups, and the bucket expires them.

A taken-over server holds the backup key, so a key that can delete lets it destroy the
backups along with the database. The key can only write and read (s3-backup-key-policy.json),
neither script ever asks the bucket to delete, and the bucket's lifecycle rule
(s3-lifecycle.json) does the expiry the backup script used to do, inside the notice's period.
"""

import json
import re
from pathlib import Path
from typing import Any, Final

import pytest
from ops_scripts import copy_ops_folder, run_with_stubs

from tracker.channel.replies import CONSENT_NOTICE

BACKUP_FOLDER: Final = Path(__file__).parent.parent / "ops" / "backup"
SCRIPTS: Final = ("backup_tracker.sh", "export_deletions.sh")
# S3 rounds each lifecycle step up to the next midnight UTC, and expiry is one step and
# removing the expired version another, so a dump can outlive the rule's days by up to two.
LIFECYCLE_ROUNDING_DAYS: Final = 2
DELETION_LIST_KEY: Final = "tracker-deletions/deleted-ids.txt"
# The only rclone commands that never remove anything from the bucket.
NON_DELETING_RCLONE_COMMANDS: Final = frozenset({"rcat", "lsf", "cat"})
DELETING_COMMAND: Final = re.compile(
    r"\brclone\s+(delete|deletefile|purge|rmdir|rmdirs|cleanup|sync|move|moveto|backend)\b|\baws\s|\bs3api\b"
)


def _load(name: str) -> dict[str, Any]:
    return json.loads((BACKUP_FOLDER / name).read_text(encoding="utf-8"))


def _statements(effect: str) -> list[dict[str, Any]]:
    return [statement for statement in _load("s3-backup-key-policy.json")["Statement"] if statement["Effect"] == effect]


def _as_list(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else value


def test_the_backup_keeps_the_period_the_notice_states() -> None:
    notice_days = re.search(r"paling lama (\d+) hari", CONSENT_NOTICE)
    assert notice_days is not None
    [rule] = _load("s3-lifecycle.json")["Rules"]
    longest_life = rule["Expiration"]["Days"] + rule["NoncurrentVersionExpiration"]["NoncurrentDays"]
    assert longest_life + LIFECYCLE_ROUNDING_DAYS <= int(notice_days.group(1))


def test_the_lifecycle_rule_covers_the_folder_the_backup_writes() -> None:
    backup_script = (BACKUP_FOLDER / "backup_tracker.sh").read_text(encoding="utf-8")
    assert 'PREFIX="tracker"' in backup_script
    assert 'REMOTE=":s3:$S3_BUCKET/$PREFIX"' in backup_script
    [rule] = _load("s3-lifecycle.json")["Rules"]
    assert (rule["Status"], rule["Filter"]["Prefix"]) == ("Enabled", "tracker/")


def test_the_lifecycle_rule_never_reaches_the_deletion_list() -> None:
    """The list must outlive every backup, so no rule may expire it (PR7)."""
    for rule in _load("s3-lifecycle.json")["Rules"]:
        assert not DELETION_LIST_KEY.startswith(rule["Filter"]["Prefix"])


def test_the_backup_key_is_allowed_no_delete() -> None:
    for statement in _statements("Allow"):
        for action in _as_list(statement["Action"]):
            assert "Delete" not in action
            assert "*" not in action


def test_the_backup_key_is_refused_every_delete_and_every_rule_change() -> None:
    """An explicit deny holds even if a broader policy is attached to the key later."""
    [deny] = _statements("Deny")
    must_be_refused = {
        "s3:DeleteObject",
        "s3:DeleteObjectVersion",
        "s3:PutBucketVersioning",
        "s3:PutLifecycleConfiguration",
    }
    assert must_be_refused <= set(_as_list(deny["Action"]))
    assert set(_as_list(deny["Resource"])) == {"arn:aws:s3:::BUCKET_NAME", "arn:aws:s3:::BUCKET_NAME/*"}


def test_the_backup_key_writes_only_the_trackers_two_folders() -> None:
    object_resources = {
        resource
        for statement in _statements("Allow")
        for resource in _as_list(statement["Resource"])
        if "/" in resource
    }
    assert object_resources == {"arn:aws:s3:::BUCKET_NAME/tracker/*", "arn:aws:s3:::BUCKET_NAME/tracker-deletions/*"}


@pytest.mark.parametrize("script_name", SCRIPTS)
def test_no_script_names_a_command_that_deletes_from_the_bucket(script_name: str) -> None:
    assert DELETING_COMMAND.search((BACKUP_FOLDER / script_name).read_text(encoding="utf-8")) is None


@pytest.mark.parametrize("script_name", SCRIPTS)
def test_no_script_asks_the_bucket_to_delete_even_with_old_dumps_there(tmp_path: Path, script_name: str) -> None:
    """The bucket lists 20 nightly dumps, which the old pruning would have cut to 14."""
    ops = copy_ops_folder(tmp_path)
    (ops / "deletions").mkdir()
    (ops / "deletions" / "deleted-ids.txt").write_text("5\n", encoding="utf-8")
    old_dumps = " ".join(f"tracker-202611{day:02d}T031700Z.dump.age" for day in range(1, 21))
    rclone = (
        f'case "$1" in lsf) printf "%s\\n" {old_dumps} deleted-ids.txt ;; cat) echo 3 ;; rcat) cat >/dev/null ;; esac'
    )
    stubs = {"docker": "", "pg_dump": "", "age": "", "rclone": rclone, "curl": ""}
    calls = run_with_stubs(ops / "backup" / script_name, tmp_path, stubs)
    rclone_commands = [call.split()[1] for call in calls if call.startswith("rclone ")]
    assert "rcat" in rclone_commands
    assert set(rclone_commands) <= NON_DELETING_RCLONE_COMMANDS
