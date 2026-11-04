"""Arthur's commands: the allow-list and exports, run with the owner's login, never by the app."""

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.ledger.deletions import (
    DeletionListError,
    cancel_confirmed_deletion,
    export_deleted_ids,
    reapply_deletions,
    run_confirmed_deletions,
)
from tracker.ledger.engine import create_engine
from tracker.ledger.export import export_person_csv
from tracker.ledger.people import create_person, delete_person, set_suspended
from tracker.ledger.retention import PurgeResult, purge_expired

OWNER_URL_ENV = "TRACKER_MIGRATOR_DATABASE_URL"
OWNER_ONLY_FILE_MODE = 0o600  # It holds someone's finances.

# Typer's rich tracebacks can print local variables, which here would include the database URL.
app = typer.Typer(no_args_is_help=True, help="Manage who may use the tracker.", pretty_exceptions_show_locals=False)


def _run_as_owner[T](work: Callable[[AsyncEngine], Awaitable[T]]) -> T:
    url = os.environ.get(OWNER_URL_ENV)
    if not url:
        raise typer.BadParameter(f"{OWNER_URL_ENV} is not set: add the owner's database URL to .env")

    async def run() -> T:
        engine = create_engine(url)
        try:
            return await work(engine)
        finally:
            await engine.dispose()

    return asyncio.run(run())


@app.command()
def add(phone: Annotated[str, typer.Argument(help="International format, digits only.")]) -> None:
    """Allow a number. It's dropped once the person's first message gives their BSUID."""
    if not phone.isascii() or not phone.isdigit():
        raise typer.BadParameter("Use digits only, in international format, such as 62812...")
    try:
        person_id = _run_as_owner(lambda engine: create_person(engine, phone=phone))
    except IntegrityError:
        typer.echo("That number is already on the list.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Added person {person_id}.")


@app.command()
def export(person_id: int, path: Path) -> None:
    """Write a person's records to a new CSV file. Needs only the database, so it works with WAHA stopped."""
    csv_text = _run_as_owner(lambda engine: export_person_csv(engine, person_id))
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, OWNER_ONLY_FILE_MODE)
    except FileExistsError:
        typer.echo(f"{path} already exists: choose a new file.", err=True)
        raise typer.Exit(code=1) from None
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as file:
        file.write(csv_text)
    typer.echo(f"Wrote {path}.")


@app.command()
def purge() -> None:
    """Delete what PR6 says is past keeping. Run it daily from cron."""
    result: PurgeResult = _run_as_owner(purge_expired)
    typer.echo(f"Removed {result.pending_entries} pending entries and {result.message_ids} message ids.")


@app.command()
def delete(
    person_id: int, yes: Annotated[bool, typer.Option("--yes", help="Confirm: this can't be undone.")] = False
) -> None:
    """Delete everything about a person, for when WhatsApp isn't there to ask through."""
    if not yes:
        typer.echo("This can't be undone. Add --yes to go ahead.", err=True)
        raise typer.Exit(code=1)
    if not _run_as_owner(lambda engine: delete_person(engine, person_id)):
        typer.echo(f"No person {person_id}.", err=True)
        raise typer.Exit(code=1)
    typer.echo("Deleted.")


@app.command("deletions-run")
def deletions_run() -> None:
    """Delete everyone who confirmed `hapus semua`. Run by export_deletions.sh every 15 minutes."""
    result = _run_as_owner(run_confirmed_deletions)
    if result.held:
        held_ids = " ".join(str(person_id) for person_id in result.held)
        typer.echo(
            f"deletions_held count={len(result.held)} ids={held_ids}: more than a real day brings. Check each, "
            "then `tracker delete ID --yes` or `tracker deletion-cancel ID`.",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"Deleted {len(result.deleted)} people.")


@app.command("deletion-cancel")
def deletion_cancel(person_id: int) -> None:
    """After a hold: let a person back in whose deletion they didn't confirm themselves."""
    if not _run_as_owner(lambda engine: cancel_confirmed_deletion(engine, person_id)):
        typer.echo(f"Person {person_id} has no confirmed deletion.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Person {person_id} can use the bot again.")


@app.command("deletions-export")
def deletions_export(path: Path) -> None:
    """Copy deleted ids to a file outside the backups. Run it often from cron (PR7)."""
    total = _run_as_owner(lambda engine: export_deleted_ids(engine, path))
    typer.echo(f"{total} deleted ids in {path}.")


@app.command("deletions-reapply")
def deletions_reapply(
    path: Path,
    empty_ok: Annotated[bool, typer.Option("--empty-ok", help="Nobody has ever been deleted.")] = False,
) -> None:
    """Right after any restore, before anyone is added: delete again everyone it brought back."""
    try:
        reapplied = _run_as_owner(lambda engine: reapply_deletions(engine, path, is_empty_ok=empty_ok))
    except DeletionListError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Deleted {reapplied} people again.")


@app.command()
def suspend(person_id: int) -> None:
    """Stop every read and write for a person, for example after a lost phone."""
    _change_suspension(person_id, is_suspended=True)


@app.command()
def resume(person_id: int) -> None:
    """Undo a suspension."""
    _change_suspension(person_id, is_suspended=False)


def _change_suspension(person_id: int, *, is_suspended: bool) -> None:
    found = _run_as_owner(lambda engine: set_suspended(engine, person_id, is_suspended=is_suspended))
    if not found:
        typer.echo(f"No person {person_id}.", err=True)
        raise typer.Exit(code=1)
    typer.echo("Done.")
