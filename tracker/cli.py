"""Arthur's commands: the allow-list and exports, run with the owner's login, never by the app."""

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.ledger.engine import create_engine
from tracker.ledger.export import export_person_csv
from tracker.ledger.people import create_person, set_suspended

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
