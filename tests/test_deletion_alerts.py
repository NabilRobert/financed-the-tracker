"""R12, T16, T20: every deletion the owner job carries out alerts Arthur, not only a hold.

A taken-over app can confirm deletions nobody asked for. Above two a day the job holds
them; at two or fewer it used to delete them silently, so a patient attacker could remove
everyone over a few days. Now each run that deletes anyone fails its heartbeat with the
count, so Arthur sees it within 15 minutes, while the backups still hold the data.
"""

from pathlib import Path

from ops_scripts import HEALTHCHECK_URL, run_export_deletions


def _deletions_run_says(output: str) -> str:
    """A docker stub whose `deletions-run` prints this, as the CLI does."""
    return f'case "$*" in *deletions-run*) echo "{output}" ;; esac'


def test_a_run_that_deletes_someone_alerts_with_the_count(tmp_path: Path) -> None:
    calls, uploaded = run_export_deletions(
        tmp_path, on_server="5\n", in_bucket=None, docker=_deletions_run_says("deletions_run deleted=2")
    )
    assert uploaded == "5\n"
    assert calls[-1] == f"curl -fsS -m 10 --retry 3 --data-raw deletions_run deleted=2 {HEALTHCHECK_URL}/fail"


def test_a_run_that_deletes_nobody_reports_success(tmp_path: Path) -> None:
    calls, _ = run_export_deletions(
        tmp_path, on_server="5\n", in_bucket="5\n", docker=_deletions_run_says("deletions_run deleted=0")
    )
    assert calls[-1].endswith(HEALTHCHECK_URL)
    assert not any("/fail" in call for call in calls)


def test_the_alert_never_names_who_was_deleted(tmp_path: Path) -> None:
    """The heartbeat service is outside the server: a count, never an id."""
    calls, _ = run_export_deletions(
        tmp_path, on_server="5\n", in_bucket=None, docker=_deletions_run_says("deletions_run deleted=1")
    )
    alert = calls[-1]
    assert "deleted=1" in alert
    assert " 5 " not in f" {alert} "


def test_a_count_from_a_terminal_still_alerts(tmp_path: Path) -> None:
    """Compose gives the container a pseudo-terminal when run by hand, which ends lines in \\r."""
    docker = 'case "$*" in *deletions-run*) printf "deletions_run deleted=1\\r\\n" ;; esac'
    calls, _ = run_export_deletions(tmp_path, on_server="5\n", in_bucket=None, docker=docker)
    assert calls[-1].endswith(f"deleted=1 {HEALTHCHECK_URL}/fail")


def test_a_run_whose_count_cannot_be_read_fails_rather_than_stay_quiet(tmp_path: Path) -> None:
    calls, uploaded = run_export_deletions(
        tmp_path, on_server="5\n", in_bucket=None, docker=_deletions_run_says("Deleted 1 people.")
    )
    assert uploaded == "5\n"
    assert calls[-1].endswith(f"{HEALTHCHECK_URL}/fail")
