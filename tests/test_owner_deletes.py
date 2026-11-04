"""M1, T16, T20: the app only records a confirmed deletion; an owner job carries it out.

The app is the only part that knows who sent a message, so a taken-over app could
confirm a deletion for anyone. It can't delete, though: the owner job does that every
15 minutes, at most two people a run. More than that waiting looks like a taken-over app,
so the job deletes none, and fails so the heartbeat alerts Arthur.
"""

import asyncio

import pytest
from conftest import Bot, database_url
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from typer.testing import CliRunner
from webhook_fixtures import new_lid

from tracker.channel.replies import DELETE_HOW, DELETED
from tracker.cli import OWNER_URL_ENV
from tracker.cli import app as cli_app
from tracker.ledger.deletions import MAX_DELETIONS_PER_RUN, cancel_confirmed_deletion, run_confirmed_deletions
from tracker.ledger.people import confirm_deletion, create_person
from tracker.ledger.scope import person_transaction

runner = CliRunner()


async def _person_exists(owner_engine: AsyncEngine, person_id: int) -> bool:
    async with owner_engine.connect() as connection:
        return await connection.scalar(text("SELECT count(*) FROM person WHERE id = :id"), {"id": person_id}) == 1


async def _confirmed_people(owner_engine: AsyncEngine, app_engine: AsyncEngine, count: int) -> list[int]:
    """People who have confirmed `hapus semua`, as the app records it."""
    people: list[int] = []
    for _ in range(count):
        person_id = await create_person(owner_engine, whatsapp_id=new_lid())
        async with person_transaction(app_engine, person_id) as scope:
            await confirm_deletion(scope)
        people.append(person_id)
    return people


async def test_the_app_login_cannot_run_the_delete_function(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, member: str
) -> None:
    async with owner_engine.connect() as connection:
        victim = await connection.scalar(text("SELECT id FROM person WHERE whatsapp_id = :w"), {"w": member})
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_engine.begin() as connection:
            await connection.execute(text("SELECT set_config('tracker.person_id', :p, true)"), {"p": str(victim)})
            await connection.execute(text("SELECT tracker_delete_person_in_scope()"))
    assert await _person_exists(owner_engine, victim)


async def test_hapus_semua_locks_the_person_out_and_says_when_it_happens(
    owner_engine: AsyncEngine, bot: Bot, member: str
) -> None:
    await bot.say(member, "hapus data saya")
    await bot.say(member, "hapus semua")
    assert bot.waha.texts[-1] == DELETED
    assert "dalam 15 menit" in DELETED
    bot.waha.texts.clear()
    await bot.say(member, "rekap")
    assert bot.waha.texts == []
    await run_confirmed_deletions(owner_engine)


async def test_the_owner_job_deletes_everyone_confirmed_up_to_the_cap(
    owner_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    people = await _confirmed_people(owner_engine, app_engine, MAX_DELETIONS_PER_RUN)
    result = await run_confirmed_deletions(owner_engine)
    assert (result.deleted, result.held) == (people, [])
    for person_id in people:
        assert not await _person_exists(owner_engine, person_id)


async def test_more_confirmed_than_the_cap_are_all_held(owner_engine: AsyncEngine, app_engine: AsyncEngine) -> None:
    people = await _confirmed_people(owner_engine, app_engine, MAX_DELETIONS_PER_RUN + 1)
    try:
        result = await run_confirmed_deletions(owner_engine)
        assert (result.deleted, result.held) == ([], people)
        for person_id in people:
            assert await _person_exists(owner_engine, person_id)
    finally:
        for person_id in people:
            await cancel_confirmed_deletion(owner_engine, person_id)


async def test_the_cli_fails_on_a_hold_so_the_heartbeat_alerts(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, database_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(OWNER_URL_ENV, database_url("tracker_owner", database_name))
    people = await _confirmed_people(owner_engine, app_engine, MAX_DELETIONS_PER_RUN + 1)
    try:
        # A thread, because the CLI runs its own event loop.
        result = await asyncio.to_thread(runner.invoke, cli_app, ["deletions-run"])
        assert result.exit_code == 1
        assert f"deletions_held count={len(people)}" in result.stderr
    finally:
        for person_id in people:
            await cancel_confirmed_deletion(owner_engine, person_id)


async def test_the_cli_deletes_when_nothing_is_held(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, database_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(OWNER_URL_ENV, database_url("tracker_owner", database_name))
    [person_id] = await _confirmed_people(owner_engine, app_engine, 1)
    result = await asyncio.to_thread(runner.invoke, cli_app, ["deletions-run"])
    assert result.exit_code == 0
    assert not await _person_exists(owner_engine, person_id)


async def test_a_cancelled_deletion_lets_the_person_back_in(owner_engine: AsyncEngine, bot: Bot, member: str) -> None:
    await bot.say(member, "hapus data saya")
    await bot.say(member, "hapus semua")
    async with owner_engine.connect() as connection:
        person_id = await connection.scalar(text("SELECT id FROM person WHERE whatsapp_id = :w"), {"w": member})
    assert await cancel_confirmed_deletion(owner_engine, person_id)
    bot.waha.texts.clear()
    await bot.say(member, "rekap")
    assert bot.waha.texts
    await bot.say(member, "hapus semua")  # The cancelled request can't be confirmed again.
    assert bot.waha.texts[-1] == DELETE_HOW


async def test_cancelling_someone_who_never_confirmed_changes_nothing(owner_engine: AsyncEngine) -> None:
    person_id = await create_person(owner_engine, whatsapp_id=new_lid())
    assert not await cancel_confirmed_deletion(owner_engine, person_id)


async def test_a_person_who_confirmed_by_number_is_not_let_back_in_by_their_lid(
    owner_engine: AsyncEngine, bot: Bot
) -> None:
    phone = "62899" + new_lid()[:7].translate(str.maketrans("abcdef", "123456"))
    await create_person(owner_engine, phone=phone)
    for message in ("halo", "setuju", "hapus data saya", "hapus semua"):
        await bot.say(f"{phone}@c.us", message)
    lid = new_lid()
    bot.waha.pair(lid, phone)
    bot.waha.texts.clear()
    await bot.say(lid, "rekap")
    assert bot.waha.texts == []
    await run_confirmed_deletions(owner_engine)
