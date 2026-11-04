"""PR7, T27: a deletion survives a restore, even one made because the server was lost.

The list of deleted ids lives outside the database, on the server and in the backup
bucket, and is re-applied after any restore. The independent review found three ways
this failed: a missing list read as empty, a cron line that could never write the list,
and a restore handing a deleted person's id to the next person added.
"""

import stat
from pathlib import Path

import pytest
from conftest import Bot, database_url
from ledger_helpers import run_the_deletion_job
from ops_scripts import HEALTHCHECK_URL, copy_ops_folder, run_with_stubs
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from typer.testing import CliRunner
from webhook_fixtures import new_lid

from tracker.cli import OWNER_URL_ENV
from tracker.cli import app as cli_app
from tracker.ledger.deletions import (
    DeletionListError,
    export_deleted_ids,
    read_deleted_ids,
    reapply_deletions,
)
from tracker.ledger.people import create_person, delete_person

REPO_ROOT = Path(__file__).parent.parent
COMPOSE = (REPO_ROOT / "ops" / "compose.yaml").read_text(encoding="utf-8")
EXPORT_SCRIPT = REPO_ROOT / "ops" / "backup" / "export_deletions.sh"
ID_BAND_START = 900_000  # Far above any id the other tests make, so resetting the counter can't collide.
runner = CliRunner()


async def _restore_person(owner_engine: AsyncEngine, person_id: int, whatsapp_id: str) -> None:
    """What restoring an older backup does: the person is back, and the list forgot them."""
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO person (id, whatsapp_id) OVERRIDING SYSTEM VALUE VALUES (:id, :w)"),
            {"id": person_id, "w": whatsapp_id},
        )
        await connection.execute(
            text("INSERT INTO category (person_id, name, direction) VALUES (:id, 'Makan', 'spending')"),
            {"id": person_id},
        )
        await connection.execute(text("DELETE FROM deleted_person WHERE person_id = :id"), {"id": person_id})


async def _person_exists(owner_engine: AsyncEngine, person_id: int) -> bool:
    async with owner_engine.connect() as connection:
        return await connection.scalar(text("SELECT count(*) FROM person WHERE id = :id"), {"id": person_id}) == 1


async def test_a_deletion_is_reapplied_after_a_restore(
    owner_engine: AsyncEngine, bot: Bot, member: str, tmp_path: Path
) -> None:
    async with owner_engine.connect() as connection:
        person_id = int(
            await connection.scalar(text("SELECT id FROM person WHERE whatsapp_id = :w"), {"w": member}) or 0
        )
    await bot.say(member, "hapus data saya")
    await bot.say(member, "hapus semua")
    await run_the_deletion_job(owner_engine)
    deleted_list = tmp_path / "deleted-ids.txt"
    await export_deleted_ids(owner_engine, deleted_list)

    await _restore_person(owner_engine, person_id, member)
    assert await _person_exists(owner_engine, person_id)
    assert await reapply_deletions(owner_engine, deleted_list) == 1
    assert not await _person_exists(owner_engine, person_id)


async def test_reapplying_twice_changes_nothing_the_second_time(owner_engine: AsyncEngine, tmp_path: Path) -> None:
    person_id = await create_person(owner_engine, whatsapp_id="reapply@lid")
    deleted_list = tmp_path / "deleted-ids.txt"
    deleted_list.write_text(f"{person_id}\n", encoding="utf-8")
    assert await reapply_deletions(owner_engine, deleted_list) == 1
    assert await reapply_deletions(owner_engine, deleted_list) == 0


async def test_the_export_keeps_ids_a_restore_has_forgotten(owner_engine: AsyncEngine, tmp_path: Path) -> None:
    deleted_list = tmp_path / "deleted-ids.txt"
    deleted_list.write_text("999999999\n", encoding="utf-8")
    await export_deleted_ids(owner_engine, deleted_list)
    assert 999_999_999 in read_deleted_ids(deleted_list)
    assert stat.S_IMODE(deleted_list.stat().st_mode) == 0o640


async def test_the_list_stays_readable_by_the_folders_group_whatever_was_there(
    owner_engine: AsyncEngine, tmp_path: Path
) -> None:
    """The owner account copies it off the server through the folder's group (R4)."""
    deleted_list = tmp_path / "deleted-ids.txt"
    leftover = tmp_path / "deleted-ids.txt.partial"
    leftover.write_text("", encoding="utf-8")
    leftover.chmod(0o600)
    await export_deleted_ids(owner_engine, deleted_list)
    assert stat.S_IMODE(deleted_list.stat().st_mode) == 0o640


def test_blank_lines_in_the_list_are_ignored(tmp_path: Path) -> None:
    deleted_list = tmp_path / "deleted-ids.txt"
    deleted_list.write_text("5\n\n  \n7\n", encoding="utf-8")
    assert read_deleted_ids(deleted_list) == {5, 7}


def test_the_first_export_starts_a_list_that_does_not_exist_yet(tmp_path: Path) -> None:
    assert read_deleted_ids(tmp_path / "none.txt") == set()


@pytest.mark.parametrize("line", ["abc", "-1", "1.5", "１", "٣"])
def test_a_list_with_a_line_that_is_not_an_id_is_refused(tmp_path: Path, line: str) -> None:
    deleted_list = tmp_path / "deleted-ids.txt"
    deleted_list.write_text(f"5\n{line}\n", encoding="utf-8")
    with pytest.raises(DeletionListError, match="line 2"):
        read_deleted_ids(deleted_list)


def test_the_cli_exports_and_reapplies(database_name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(OWNER_URL_ENV, database_url("tracker_owner", database_name))
    deleted_list = tmp_path / "deleted-ids.txt"
    assert runner.invoke(cli_app, ["deletions-export", str(deleted_list)]).exit_code == 0
    assert runner.invoke(cli_app, ["deletions-reapply", "--empty-ok", str(deleted_list)]).exit_code == 0
    deleted_list.write_text("not an id\n", encoding="utf-8")
    result = runner.invoke(cli_app, ["deletions-reapply", str(deleted_list)])
    assert result.exit_code == 1
    assert "isn't a person id" in result.stderr


# A missing or empty list must never pass for "nobody to delete".
async def test_reapplying_a_list_that_does_not_exist_is_refused(owner_engine: AsyncEngine, tmp_path: Path) -> None:
    with pytest.raises(DeletionListError, match="doesn't exist"):
        await reapply_deletions(owner_engine, tmp_path / "never-written" / "deleted-ids.txt")


async def test_reapplying_an_empty_list_is_refused(owner_engine: AsyncEngine, tmp_path: Path) -> None:
    deleted_list = tmp_path / "deleted-ids.txt"
    deleted_list.write_text("", encoding="utf-8")
    with pytest.raises(DeletionListError, match="empty"):
        await reapply_deletions(owner_engine, deleted_list)


async def test_an_empty_list_is_accepted_when_said_to_be_expected(owner_engine: AsyncEngine, tmp_path: Path) -> None:
    deleted_list = tmp_path / "deleted-ids.txt"
    deleted_list.write_text("", encoding="utf-8")
    assert await reapply_deletions(owner_engine, deleted_list, is_empty_ok=True) == 0


def test_the_cli_refuses_a_missing_list(database_name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(OWNER_URL_ENV, database_url("tracker_owner", database_name))
    result = runner.invoke(cli_app, ["deletions-reapply", str(tmp_path / "missing.txt")])
    assert result.exit_code == 1
    assert "doesn't exist" in result.stderr


# M4: a restore resets the id counter, so a deleted id could go to the next person added.
async def _simulate_a_restore_from_before(owner_engine: AsyncEngine, person_id: int) -> None:
    """pg_restore sets the id counter back, and the restored list never heard of the deletion."""
    async with owner_engine.begin() as connection:
        await connection.execute(text("DELETE FROM deleted_person WHERE person_id = :id"), {"id": person_id})
        await connection.execute(text(f"ALTER TABLE person ALTER COLUMN id RESTART WITH {ID_BAND_START}"))


async def _leaver_in_the_id_band(owner_engine: AsyncEngine, tmp_path: Path) -> tuple[int, Path]:
    async with owner_engine.begin() as connection:
        await connection.execute(text(f"ALTER TABLE person ALTER COLUMN id RESTART WITH {ID_BAND_START}"))
    leaver = await create_person(owner_engine, whatsapp_id=new_lid())
    await delete_person(owner_engine, leaver)
    deleted_list = tmp_path / "deleted-ids.txt"
    await export_deleted_ids(owner_engine, deleted_list)
    await _simulate_a_restore_from_before(owner_engine, leaver)
    return leaver, deleted_list


async def test_after_a_restore_and_reapply_nobody_new_gets_a_deleted_id(
    owner_engine: AsyncEngine, tmp_path: Path
) -> None:
    leaver, deleted_list = await _leaver_in_the_id_band(owner_engine, tmp_path)
    await reapply_deletions(owner_engine, deleted_list)
    newcomer_lid = new_lid()
    newcomer = await create_person(owner_engine, whatsapp_id=newcomer_lid)
    assert newcomer > leaver
    await reapply_deletions(owner_engine, deleted_list)
    async with owner_engine.connect() as connection:
        found = await connection.scalar(text("SELECT id FROM person WHERE whatsapp_id = :w"), {"w": newcomer_lid})
    assert found == newcomer


async def test_reapplying_records_every_listed_id_as_deleted_again(owner_engine: AsyncEngine, tmp_path: Path) -> None:
    leaver, deleted_list = await _leaver_in_the_id_band(owner_engine, tmp_path)
    await reapply_deletions(owner_engine, deleted_list)
    async with owner_engine.connect() as connection:
        recorded = await connection.scalar(
            text("SELECT count(*) FROM deleted_person WHERE person_id = :id"), {"id": leaver}
        )
    assert recorded == 1


# The list is written where cron can reach it, and copied off the server.
def test_the_cli_container_mounts_the_deletion_list_folder() -> None:
    start = COMPOSE.index("\n  cli:\n")
    assert "- ./deletions:/deletions" in COMPOSE[start:]


def test_the_deletion_list_folder_is_never_committed() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "ops/deletions/" in gitignore


def _run_export(tmp_path: Path, *, on_server: str, in_bucket: str | None, docker: str = "") -> tuple[list[str], str]:
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


def test_the_export_uploads_the_list_then_reports_success(tmp_path: Path) -> None:
    calls, uploaded = _run_export(tmp_path, on_server="5\n7\n", in_bucket=None)
    assert calls[1].endswith("deletions-run")
    assert calls[2].endswith("deletions-export /deletions/deleted-ids.txt")
    assert calls[-2] == "rclone rcat :s3:example-bucket/tracker-deletions/deleted-ids.txt"
    assert calls[-1].endswith(HEALTHCHECK_URL)
    assert uploaded == "5\n7\n"


def test_the_upload_keeps_every_id_the_bucket_already_has(tmp_path: Path) -> None:
    """A server rebuilt from an old backup has a short list; it must never shrink the bucket's."""
    _, uploaded = _run_export(tmp_path, on_server="5\n7\n", in_bucket="3\n9\n5\n")
    assert uploaded == "3\n5\n7\n9\n"


def test_nothing_is_uploaded_when_the_bucket_already_has_every_id(tmp_path: Path) -> None:
    calls, uploaded = _run_export(tmp_path, on_server="5\n", in_bucket="5\n3\n")
    assert uploaded == ""
    assert not any(call.startswith("rclone rcat") for call in calls)
    assert calls[-1].endswith(HEALTHCHECK_URL)


def test_a_hold_still_exports_and_uploads_then_reports_failure(tmp_path: Path) -> None:
    """A `tracker delete` made by hand during a hold must still reach the bucket."""
    docker = 'case "$*" in *deletions-run*) exit 1 ;; esac'
    calls, uploaded = _run_export(tmp_path, on_server="5\n", in_bucket=None, docker=docker)
    assert any(call.endswith("deletions-export /deletions/deleted-ids.txt") for call in calls)
    assert uploaded == "5\n"
    assert calls[-1].endswith(f"{HEALTHCHECK_URL}/fail")


def test_a_failed_export_uploads_nothing_and_reports_failure(tmp_path: Path) -> None:
    docker = 'case "$*" in *deletions-export*) exit 1 ;; esac'
    calls, uploaded = _run_export(tmp_path, on_server="5\n", in_bucket=None, docker=docker)
    assert not any(call.startswith("rclone") for call in calls)
    assert calls[-1].endswith(f"{HEALTHCHECK_URL}/fail")
    assert uploaded == ""


@pytest.mark.parametrize("failing_step", ["lsf", "cat"])
def test_an_unreadable_bucket_uploads_nothing_and_reports_failure(tmp_path: Path, failing_step: str) -> None:
    """If the bucket's copy can't be listed or read, uploading the server's list could shrink it."""
    ops = copy_ops_folder(tmp_path)
    (ops / "deletions").mkdir()
    (ops / "deletions" / "deleted-ids.txt").write_text("5\n", encoding="utf-8")
    rclone = f'case "$1" in {failing_step}) exit 1 ;; lsf) echo deleted-ids.txt ;; esac'
    script = ops / "backup" / "export_deletions.sh"
    calls = run_with_stubs(script, tmp_path, {"docker": "", "rclone": rclone, "curl": ""})
    assert not any(call.startswith("rclone rcat") for call in calls)
    assert calls[-1].endswith(f"{HEALTHCHECK_URL}/fail")


def test_the_copy_off_the_server_is_out_of_reach_of_the_backup_pruning() -> None:
    backup_script = (REPO_ROOT / "ops" / "backup" / "backup_tracker.sh").read_text(encoding="utf-8")
    assert 'REMOTE=":s3:$S3_BUCKET/$PREFIX"' in backup_script
    assert 'PREFIX="tracker"' in backup_script
    assert "tracker-deletions/" in EXPORT_SCRIPT.read_text(encoding="utf-8")


async def test_reapplying_never_moves_the_id_counter_back(owner_engine: AsyncEngine, tmp_path: Path) -> None:
    leaver = await create_person(owner_engine, whatsapp_id=new_lid())
    await delete_person(owner_engine, leaver)
    newer = await create_person(owner_engine, whatsapp_id=new_lid())
    deleted_list = tmp_path / "deleted-ids.txt"
    deleted_list.write_text(f"{leaver}\n", encoding="utf-8")
    await reapply_deletions(owner_engine, deleted_list)
    assert await create_person(owner_engine, whatsapp_id=new_lid()) > newer
