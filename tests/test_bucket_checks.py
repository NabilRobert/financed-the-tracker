"""R18, R19, T45: the drill's checks of the bucket, run on Arthur's machine with his own login.

A taken-over server can't delete a backup, but it can overwrite one, and overwrite the
deletion list with a shorter one. Versioning keeps the old copies, so check_bucket.sh
reads them: a dump with two copies was overwritten, and the deletion list it writes is
every id any copy ever held, because the list only ever grows.
"""

import subprocess
from pathlib import Path

from ops_scripts import copy_ops_folder, run_with_stubs_and_result

BUCKET = "example-bucket"
LIST_KEY = "tracker-deletions/deleted-ids.txt"
CLEAN_DUMPS = "tracker/tracker-20261101T031700Z.dump.age\ttracker/tracker-20261102T031700Z.dump.age"
OVERWRITTEN_DUMP = "tracker/tracker-20261102T031700Z.dump.age"


def _check_bucket(
    tmp_path: Path, *, dump_keys: str, list_copies: dict[str, str]
) -> tuple[list[str], subprocess.CompletedProcess[str], Path]:
    """Runs check_bucket.sh against a stub `aws` whose bucket holds these dump versions and
    these copies of the deletion list (version id: contents). Returns the calls, the result
    and where the merged list goes."""
    copies = tmp_path / "copies"
    copies.mkdir()
    for version_id, contents in list_copies.items():
        (copies / version_id).write_text(contents, encoding="utf-8")
    version_ids = "\t".join(list_copies) or "None"
    aws = f"""case "$*" in
  *"--prefix tracker/ "*) printf '{dump_keys}\\n' ;;
  *list-object-versions*) printf '{version_ids}\\n' ;;
  *get-object*)
    previous=""; for argument; do [ "$previous" = "--version-id" ] && version="$argument"; previous="$argument"; done
    cp "{copies}/$version" "$argument" ;;
esac"""
    merged_list = tmp_path / "merged.txt"
    script = copy_ops_folder(tmp_path) / "backup" / "check_bucket.sh"
    calls, result = run_with_stubs_and_result(script, tmp_path, {"aws": aws}, arguments=(BUCKET, str(merged_list)))
    return calls, result, merged_list


def test_the_merged_list_keeps_every_id_an_older_copy_had(tmp_path: Path) -> None:
    """R19: an attacker overwrote the list with a shorter one; the old copy still counts."""
    copies = {"older": "3\n5\n9\n", "newer": "5\n"}
    _, result, merged_list = _check_bucket(tmp_path, dump_keys=CLEAN_DUMPS, list_copies=copies)
    assert result.returncode == 0
    assert merged_list.read_text(encoding="utf-8") == "3\n5\n9\n"


def test_a_dump_with_two_copies_fails_the_check_and_is_named(tmp_path: Path) -> None:
    """R18: dump names carry the time they were made, so a second copy means an overwrite."""
    dump_keys = f"{CLEAN_DUMPS}\t{OVERWRITTEN_DUMP}"
    _, result, merged_list = _check_bucket(tmp_path, dump_keys=dump_keys, list_copies={"only": "3\n"})
    assert result.returncode != 0
    assert OVERWRITTEN_DUMP in result.stderr
    assert merged_list.read_text(encoding="utf-8") == "3\n"


def test_dumps_with_one_copy_each_pass(tmp_path: Path) -> None:
    _, result, _ = _check_bucket(tmp_path, dump_keys=CLEAN_DUMPS, list_copies={"only": "3\n"})
    assert result.returncode == 0
    assert "overwritten" not in result.stderr


def test_a_bucket_with_no_deletion_list_fails_rather_than_merge_nothing(tmp_path: Path) -> None:
    _, result, merged_list = _check_bucket(tmp_path, dump_keys=CLEAN_DUMPS, list_copies={})
    assert result.returncode != 0
    assert LIST_KEY in result.stderr
    assert not merged_list.exists()


def test_the_check_only_reads_the_bucket(tmp_path: Path) -> None:
    calls, _, _ = _check_bucket(tmp_path, dump_keys=CLEAN_DUMPS, list_copies={"older": "3\n", "newer": "3\n"})
    commands = {call.split()[2] for call in calls}
    assert commands == {"list-object-versions", "get-object"}
