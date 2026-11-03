"""Slice 5's local half: the image, the compose file, the backup and deletions surviving a restore.

The server half (probes, a restore drill, permissions) is in docs/runbook-deploy.md, and
each of its checks is a line of the real-data gate.
"""

import re
import stat
from pathlib import Path

import pytest
from conftest import Bot, database_url
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from typer.testing import CliRunner

from tracker.cli import OWNER_URL_ENV
from tracker.cli import app as cli_app
from tracker.ledger.deletions import DeletionListError, export_deleted_ids, read_deleted_ids, reapply_deletions
from tracker.ledger.people import create_person
from tracker.ledger.summaries import today_in_jakarta
from tracker.serve import DEFAULT_BIND_HOST, bind_host

REPO_ROOT = Path(__file__).parent.parent
DOCKERFILE = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (REPO_ROOT / "ops" / "compose.yaml").read_text(encoding="utf-8")
BACKUP_SCRIPT = (REPO_ROOT / "ops" / "backup" / "backup_tracker.sh").read_text(encoding="utf-8")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")
runner = CliRunner()


def _service(name: str) -> str:
    """One service's block of the compose file, up to the next top-level key in it."""
    start = COMPOSE.index(f"\n  {name}:\n")
    end = re.search(r"\n  [a-z]+:\n|\nnetworks:", COMPOSE[start + 1 :])
    assert end is not None
    return COMPOSE[start : start + 1 + end.start()]


# The image.
def test_every_base_image_is_pinned_by_digest() -> None:
    images = re.findall(r"^FROM (\S+)", DOCKERFILE, re.MULTILINE)
    assert images
    assert all(DIGEST.search(image) for image in images)


def test_the_app_runs_as_a_user_who_is_not_root() -> None:
    assert re.search(r"^USER 10001$", DOCKERFILE, re.MULTILINE)
    assert DOCKERFILE.rindex("USER 10001") > DOCKERFILE.rindex("FROM ")


def test_the_image_holds_no_secrets_or_tests() -> None:
    ignored = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ignored.splitlines()[1] == "*"
    allowed = {line.removeprefix("!") for line in ignored.splitlines() if line.startswith("!")}
    assert not any(".env" in name or "test" in name or "session" in name for name in allowed)


def test_the_dates_work_without_the_systems_timezone_files() -> None:
    """The slim image has no zone files, so zoneinfo must find them in the tzdata package."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"tzdata==' in pyproject
    assert today_in_jakarta() is not None


# The compose file.
def test_the_app_never_gets_the_owners_login() -> None:
    app = _service("app")
    assert "env_file: .env.app" in app
    assert "MIGRATOR" not in app
    assert ".env.owner" not in app


def _published_ports() -> list[str]:
    """Every entry under any `ports:` key, however it's written."""
    entries: list[str] = []
    for block in re.findall(r"\n    ports:\n((?:      - .*\n)+)", COMPOSE):
        entries.extend(line.strip().removeprefix("- ").strip('"') for line in block.splitlines())
    return entries


def test_only_loopback_ports_are_published() -> None:
    ports = _published_ports()
    assert len(ports) == 2
    assert all(entry.startswith("127.0.0.1:") for entry in ports)


def test_the_app_container_is_locked_down() -> None:
    app = _service("app")
    for setting in ("read_only: true", "cap_drop: [ALL]", '"no-new-privileges:true"', "mem_limit: 256m"):
        assert setting in app


def test_waha_is_pinned_and_private() -> None:
    waha = _service("waha")
    assert DIGEST.search(waha)
    assert 'WAHA_DASHBOARD_ENABLED: "False"' in waha
    assert 'WHATSAPP_SWAGGER_ENABLED: "False"' in waha
    assert "WAHA_API_KEY: ${WAHA_API_KEY_SHA512:?" in waha
    assert "WAHA_NO_API_KEY" not in COMPOSE
    assert "mem_limit: 512m" in waha


def test_waha_runs_locked_down_and_not_as_root() -> None:
    waha = _service("waha")
    for setting in ('user: "10001:10001"', "read_only: true", "cap_drop: [ALL]", '"no-new-privileges:true"'):
        assert setting in waha


def test_waha_never_generates_a_password_to_print() -> None:
    """Unset, WAHA invents panel passwords and writes them to its log, which monitoring ships."""
    waha = _service("waha")
    for name in ("WAHA_DASHBOARD_PASSWORD", "WHATSAPP_SWAGGER_PASSWORD"):
        assert f"{name}: ${{WAHA_UNUSED_PANEL_PASSWORD:?" in waha


def test_waha_calls_the_app_over_the_private_network() -> None:
    session = (REPO_ROOT / "ops" / "waha" / "session.example.json").read_text(encoding="utf-8")
    assert '"url": "http://app:8099/webhook"' in session
    assert "WAHA_URL: http://waha:3000" in _service("app")


def test_session_files_can_never_be_committed_or_built_in() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "ops/waha-sessions/" in gitignore
    assert "./waha-sessions:/app/.sessions" in _service("waha")


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])  # noqa: S104 - the two allowed values.
def test_the_app_binds_only_where_it_is_allowed(host: str) -> None:
    assert bind_host({"TRACKER_BIND_HOST": host}) == host


@pytest.mark.parametrize("host", ["::", "192.168.1.5", "", "localhost"])
def test_any_other_bind_address_stops_startup(host: str) -> None:
    with pytest.raises(ValueError, match="TRACKER_BIND_HOST"):
        bind_host({"TRACKER_BIND_HOST": host})


def test_the_app_binds_to_loopback_unless_told_otherwise() -> None:
    assert bind_host({}) == DEFAULT_BIND_HOST == "127.0.0.1"


# The backup.
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


def test_whatsapps_session_files_are_not_backed_up() -> None:
    assert ".sessions" not in BACKUP_SCRIPT.replace("WAHA's session files", "")


# PR7, T27: deletions survive a restore.
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
    assert stat.S_IMODE(deleted_list.stat().st_mode) == 0o600


def test_blank_lines_in_the_list_are_ignored(tmp_path: Path) -> None:
    deleted_list = tmp_path / "deleted-ids.txt"
    deleted_list.write_text("5\n\n  \n7\n", encoding="utf-8")
    assert read_deleted_ids(deleted_list) == {5, 7}


def test_a_missing_list_means_nothing_to_reapply(tmp_path: Path) -> None:
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
    assert runner.invoke(cli_app, ["deletions-reapply", str(deleted_list)]).exit_code == 0
    deleted_list.write_text("not an id\n", encoding="utf-8")
    result = runner.invoke(cli_app, ["deletions-reapply", str(deleted_list)])
    assert result.exit_code == 1
    assert "isn't a person id" in result.stderr


async def test_each_message_logs_a_content_free_heartbeat(
    bot: Bot, member: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The dead-man alert watches for this line, so it must name the outcome and nothing else."""
    with caplog.at_level("INFO", logger="tracker.channel"):
        await bot.say(member, "makan 25rb")
    assert "message_handled outcome=entry" in caplog.text


def test_the_runbook_names_every_server_check() -> None:
    runbook = (REPO_ROOT / "docs" / "runbook-deploy.md").read_text(encoding="utf-8")
    for step in ("deletions-reapply", "deletions-export", "backup_tracker.sh", "probe", "sentinel", "QR"):
        assert step in runbook
