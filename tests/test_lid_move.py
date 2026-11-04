"""A person first linked by number moves onto their LID when WAHA learns it (M2, T12).

WAHA can't always pair a number with a LID at first contact, so some people are linked
as `<number>@c.us`. Once WAHA learns their LID, their messages arrive as that LID, which
matched nobody, so the person went silent. They now move onto the LID. Arthur accepted
in writing that this hands the ledger to whoever holds the number at that moment
(docs/planning/threat-model.md, T12).
"""

import asyncio
import uuid

from conftest import Bot
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from webhook_fixtures import new_lid

from tracker.ledger.people import create_person, resolve_sender, set_suspended

PHONE_SUFFIX = "@c.us"
MOVE_LOCK_WAIT_SECONDS = 0.3  # Long enough for the second call to reach the locked row.


def _new_phone() -> str:
    """An invented Indonesian-looking number, different in every test."""
    return "62899" + str(uuid.uuid4().int)[:7]


async def _person_linked_by_number(owner_engine: AsyncEngine, bot: Bot) -> tuple[int, str]:
    """Someone who joined before WAHA knew their LID, and agreed to the notice."""
    phone = _new_phone()
    person_id = await create_person(owner_engine, phone=phone)
    await bot.say(f"{phone}{PHONE_SUFFIX}", "halo")
    await bot.say(f"{phone}{PHONE_SUFFIX}", "setuju")
    bot.waha.texts.clear()
    return person_id, phone


async def _whatsapp_id_of(owner_engine: AsyncEngine, person_id: int) -> str | None:
    async with owner_engine.connect() as connection:
        return await connection.scalar(text("SELECT whatsapp_id FROM person WHERE id = :id"), {"id": person_id})


async def test_a_person_linked_by_number_is_moved_onto_their_lid(owner_engine: AsyncEngine, bot: Bot) -> None:
    person_id, phone = await _person_linked_by_number(owner_engine, bot)
    lid = new_lid()
    bot.waha.pair(lid, phone)
    await bot.say(lid, "makan 25rb")
    assert bot.waha.texts
    assert await _whatsapp_id_of(owner_engine, person_id) == lid


async def test_after_the_move_their_number_still_reaches_them(owner_engine: AsyncEngine, bot: Bot) -> None:
    person_id, phone = await _person_linked_by_number(owner_engine, bot)
    lid = new_lid()
    bot.waha.pair(lid, phone)
    await bot.say(lid, "makan 25rb")
    bot.waha.texts.clear()
    await bot.say(f"{phone}{PHONE_SUFFIX}", "makan 30rb")
    assert bot.waha.texts
    assert await _whatsapp_id_of(owner_engine, person_id) == lid


async def test_a_suspended_person_is_not_moved(owner_engine: AsyncEngine, bot: Bot) -> None:
    person_id, phone = await _person_linked_by_number(owner_engine, bot)
    await set_suspended(owner_engine, person_id, is_suspended=True)
    lid = new_lid()
    bot.waha.pair(lid, phone)
    await bot.say(lid, "makan 25rb")
    assert bot.waha.texts == []
    assert await _whatsapp_id_of(owner_engine, person_id) == f"{phone}{PHONE_SUFFIX}"


async def test_a_lid_with_no_number_moves_nobody(owner_engine: AsyncEngine, bot: Bot) -> None:
    person_id, _ = await _person_linked_by_number(owner_engine, bot)
    await bot.say(new_lid(), "makan 25rb")
    assert bot.waha.texts == []
    assert (await _whatsapp_id_of(owner_engine, person_id) or "").endswith(PHONE_SUFFIX)


async def test_a_second_message_during_the_move_still_finds_the_person(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, bot: Bot
) -> None:
    """Two messages at once as the new LID: the one that waits on the move must not be a stranger."""
    person_id, phone = await _person_linked_by_number(owner_engine, bot)
    lid = new_lid()
    async with app_engine.connect() as first_message:
        transaction = await first_message.begin()
        moved = await first_message.scalar(
            text("SELECT tracker_resolve_sender(:id, :phone)"), {"id": lid, "phone": phone}
        )
        assert moved == person_id
        second_message = asyncio.create_task(resolve_sender(app_engine, lid, phone))
        await asyncio.sleep(MOVE_LOCK_WAIT_SECONDS)  # The second now waits on the first's row lock.
        await transaction.commit()
    assert await second_message == person_id
