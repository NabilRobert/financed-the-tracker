"""Slice 5's local half: the image and the compose file. The backup and deletions have their own files.

The server half (probes, a restore drill, permissions) is in docs/runbook-deploy.md, and
each of its checks is a line of the real-data gate.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import Bot

from tracker.ledger.summaries import today_in_jakarta
from tracker.serve import DEFAULT_BIND_HOST, bind_host, configure_logging

REPO_ROOT = Path(__file__).parent.parent
DOCKERFILE = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (REPO_ROOT / "ops" / "compose.yaml").read_text(encoding="utf-8")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")


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


def test_the_monitoring_stack_reaches_the_app_without_a_published_port() -> None:
    assert "aliases: [tracker-app]" in _service("app")
    assert "name: systems-monitoring_default" in COMPOSE
    assert "external: true" in COMPOSE


def test_the_app_writes_its_log_where_monitoring_reads_it_without_the_docker_socket() -> None:
    app = _service("app")
    assert "TRACKER_LOG_FILE: /var/log/tracker/app.log" in app
    assert "/var/log/tracker:/var/log/tracker" in app
    assert "docker.sock" not in COMPOSE


def test_log_lines_reach_the_file_when_one_is_set(tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    try:
        configure_logging({"TRACKER_LOG_FILE": str(log_file)})
        logging.getLogger("tracker.channel").info("message_handled outcome=entry")
        for handler in logging.getLogger().handlers:
            handler.flush()
        assert "INFO tracker.channel message_handled outcome=entry" in log_file.read_text(encoding="utf-8")
    finally:
        for handler in logging.getLogger().handlers:
            handler.close()
        logging.getLogger().handlers.clear()


def test_without_a_log_file_only_stdout_is_used() -> None:
    try:
        configure_logging({})
        assert [type(handler) for handler in logging.getLogger().handlers] == [logging.StreamHandler]
    finally:
        logging.getLogger().handlers.clear()


def test_the_app_container_is_locked_down() -> None:
    app = _service("app")
    for setting in ("read_only: true", "cap_drop: [ALL]", '"no-new-privileges:true"', "mem_limit: 256m"):
        assert setting in app


def test_the_env_example_lists_every_variable_compose_requires() -> None:
    """Cron runs compose with an empty environment, so every value must come from ops/.env (R16)."""
    required = set(re.findall(r"\$\{([A-Z0-9_]+):\?", COMPOSE))
    example = (REPO_ROOT / "ops" / ".env.example").read_text(encoding="utf-8")
    listed = set(re.findall(r"^([A-Z0-9_]+)=", example, flags=re.MULTILINE))
    assert listed == required


def test_compose_works_from_crons_empty_environment(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker isn't installed here")
    ops = tmp_path / "ops"
    ops.mkdir()
    shutil.copy(REPO_ROOT / "ops" / "compose.yaml", ops / "compose.yaml")
    for env_file in (".env.app", ".env.owner"):
        (ops / env_file).touch()
    sample = {
        "TRACKER_VERSION": "test",
        "TRACKER_NETWORK_SUBNET": "10.99.0.0/24",
        "WAHA_API_KEY_SHA512": "sha512:00",
        "WAHA_UNUSED_PANEL_PASSWORD": "unused",
    }
    (ops / ".env").write_text("".join(f"{key}={value}\n" for key, value in sample.items()), encoding="utf-8")
    command = [docker, "compose", "-f", str(ops / "compose.yaml"), "--profile", "tools", "config", "--quiet"]
    result = subprocess.run(command, env={"PATH": os.environ["PATH"]}, capture_output=True, text=True, check=False)  # noqa: S603 - fixed arguments.
    assert result.returncode == 0, result.stderr


def test_the_apps_network_is_pinned_so_postgres_can_allow_it_alone() -> None:
    networks = COMPOSE[COMPOSE.index("\nnetworks:") :]
    assert "- subnet: ${TRACKER_NETWORK_SUBNET:?" in networks


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


async def test_each_message_logs_a_content_free_heartbeat(
    bot: Bot, member: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The dead-man alert watches for this line, so it must name the outcome and nothing else."""
    with caplog.at_level("INFO", logger="tracker.channel"):
        await bot.say(member, "makan 25rb")
    assert "message_handled outcome=entry" in caplog.text


def test_the_runbook_names_every_server_check() -> None:
    runbook = (REPO_ROOT / "docs" / "runbook-deploy.md").read_text(encoding="utf-8")
    for step in (
        "deletions-reapply",
        "deletions-export",
        "backup_tracker.sh",
        "export_deletions.sh",
        "deletions-run",
        "tracker deletion-cancel <id>",
        "DELETE FROM deleted_person WHERE person_id = <id>",
        "install -d -m 2750 -o 10001 -g <owner account's group> <repo>/ops/deletions",
        "Leave the bucket's versioning off",
        "the server's Postgres log",
        "BACKUP_PGPASSFILE",
        "age -d -i <key> tracker-*.dump.age | pg_restore -h 127.0.0.1",
        "uv run tracker deletions-reapply <merged list>",
        "ssh -L <port>:127.0.0.1:5432 <server>",
        'sed -i "s/^TRACKER_VERSION=.*/TRACKER_VERSION=$(git rev-parse HEAD)/" ops/.env',
        "Docker Engine is 28 or later",
        "rclone lsf --s3-versions :s3:<bucket>/tracker",
        "DELETIONS_HEALTHCHECK_URL",
        "probe",
        "sentinel",
        "QR",
        "install -d -m 755 -o 10001 /var/log/tracker",
        "stat -c '%a' /var/log/tracker /var/log/tracker/app.log",
    ):
        assert step in runbook
