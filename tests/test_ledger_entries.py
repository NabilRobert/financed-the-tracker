"""Writing and undoing entries: exact, repeat-safe, and never erased (L2 to L5, ADR-013)."""

import asyncio
from datetime import date

import pytest
from ledger_helpers import A_DAY, add_entry, income, new_person, spending
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from tracker.ledger.categories import CategoryChange, change_entry_category
from tracker.ledger.entries import EntrySource, VoidOutcome, void_entry, write_entry
from tracker.ledger.scope import person_transaction
from tracker.parsing.amount import MAX_ENTRY_RUPIAH
from tracker.parsing.message import ParsedEntry, parse_message


@pytest.fixture
async def person(owner_engine: AsyncEngine) -> int:
    return await new_person(owner_engine)


async def stored_entries(owner_engine: AsyncEngine, person_id: int) -> list[tuple[int, str, str, str, date, str, bool]]:
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text("""
                SELECT e.amount_rupiah, e.direction, c.name, e.description, e.occurred_on, e.source,
                       e.voided_at IS NOT NULL AS is_voided
                FROM entry e JOIN category c ON c.id = e.category_id
                WHERE e.person_id = :id ORDER BY e.id
            """),
            {"id": person_id},
        )
    return [tuple(row) for row in rows]


async def test_an_entry_is_stored_exactly(owner_engine: AsyncEngine, app_engine: AsyncEngine, person: int) -> None:
    written = await add_entry(app_engine, person, spending(25_000, "Makan", "makan siang"))
    assert written.category == "Makan"
    assert written.budget_warning is None
    assert await stored_entries(owner_engine, person) == [
        (25_000, "spending", "Makan", "makan siang", A_DAY, "rules", False)
    ]


async def test_the_source_is_recorded(owner_engine: AsyncEngine, app_engine: AsyncEngine, person: int) -> None:
    async with person_transaction(app_engine, person) as scope:
        await write_entry(scope, f"wamid.source-{person}", spending(1_000), A_DAY, EntrySource.MODEL)
    assert (await stored_entries(owner_engine, person))[0][5] == "model"


async def test_the_largest_allowed_amount_comes_back_exact(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, person: int
) -> None:
    await add_entry(app_engine, person, spending(MAX_ENTRY_RUPIAH))
    assert (await stored_entries(owner_engine, person))[0][0] == MAX_ENTRY_RUPIAH


async def test_a_repeat_delivery_adds_nothing(owner_engine: AsyncEngine, app_engine: AsyncEngine, person: int) -> None:
    async with person_transaction(app_engine, person) as scope:
        assert await write_entry(scope, f"wamid.repeat-{person}", spending(25_000), A_DAY) is not None
    async with person_transaction(app_engine, person) as scope:
        assert await write_entry(scope, f"wamid.repeat-{person}", spending(25_000), A_DAY) is None
    assert len(await stored_entries(owner_engine, person)) == 1


async def test_a_repeat_delivery_after_undo_does_not_bring_the_entry_back(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, person: int
) -> None:
    async with person_transaction(app_engine, person) as scope:
        written = await write_entry(scope, f"wamid.undone-{person}", spending(25_000), A_DAY)
    assert written is not None
    async with person_transaction(app_engine, person) as scope:
        await void_entry(scope, written.entry_id)
    async with person_transaction(app_engine, person) as scope:
        assert await write_entry(scope, f"wamid.undone-{person}", spending(25_000), A_DAY) is None
    assert [row[6] for row in await stored_entries(owner_engine, person)] == [True]


async def test_simultaneous_deliveries_of_one_message_add_one_entry(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, person: int
) -> None:
    async def deliver() -> bool:
        async with person_transaction(app_engine, person) as scope:
            return await write_entry(scope, f"wamid.race-{person}", spending(25_000), A_DAY) is not None

    results = await asyncio.gather(*[deliver() for _ in range(5)])
    assert sorted(results) == [False, False, False, False, True]
    assert len(await stored_entries(owner_engine, person)) == 1


async def test_the_database_allows_one_entry_per_message(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, person: int
) -> None:
    written = await add_entry(app_engine, person, spending(1_000))
    with pytest.raises(IntegrityError, match="inbound_message_id"):
        async with owner_engine.begin() as connection:
            await connection.execute(
                text("""
                    INSERT INTO entry (person_id, inbound_message_id, direction, amount_rupiah, category_id,
                                       description, occurred_on, source)
                    SELECT person_id, inbound_message_id, direction, amount_rupiah, category_id,
                           description, occurred_on, source
                    FROM entry WHERE id = :id
                """),
                {"id": written.entry_id},
            )


async def test_a_failed_write_leaves_no_message_behind(app_engine: AsyncEngine, person: int) -> None:
    """The message id and the entry commit together, or a retry would be taken for a repeat."""
    with pytest.raises(IntegrityError):
        async with person_transaction(app_engine, person) as scope:
            await write_entry(scope, f"wamid.fail-{person}", spending(MAX_ENTRY_RUPIAH + 1), A_DAY)
    async with person_transaction(app_engine, person) as scope:
        assert await write_entry(scope, f"wamid.fail-{person}", spending(1_000), A_DAY) is not None


@pytest.mark.parametrize("bad_amount", [0, -1, MAX_ENTRY_RUPIAH + 1])
async def test_the_database_refuses_an_amount_outside_the_limits(
    app_engine: AsyncEngine, person: int, bad_amount: int
) -> None:
    with pytest.raises(IntegrityError, match="amount_rupiah"):
        async with person_transaction(app_engine, person) as scope:
            await write_entry(scope, f"wamid.bad-{person}-{bad_amount}", spending(bad_amount), A_DAY)


async def test_a_message_with_a_nul_byte_reaches_the_database_safely(app_engine: AsyncEngine, person: int) -> None:
    """Postgres text can't hold a NUL. One message must not be able to crash the write."""
    parsed = parse_message("makan\x00 siang 25rb")
    assert isinstance(parsed, ParsedEntry)
    await add_entry(app_engine, person, parsed)


async def test_undo_marks_the_entry_and_keeps_it(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, person: int
) -> None:
    written = await add_entry(app_engine, person, spending(25_000))
    async with person_transaction(app_engine, person) as scope:
        assert await void_entry(scope, written.entry_id) is VoidOutcome.VOIDED
    assert [row[0] for row in await stored_entries(owner_engine, person)] == [25_000]
    assert [row[6] for row in await stored_entries(owner_engine, person)] == [True]


async def test_undo_is_safe_to_repeat(app_engine: AsyncEngine, owner_engine: AsyncEngine, person: int) -> None:
    written = await add_entry(app_engine, person, spending(25_000))
    async with person_transaction(app_engine, person) as scope:
        await void_entry(scope, written.entry_id)
    async with owner_engine.connect() as connection:
        first_voided_at = await connection.scalar(
            text("SELECT voided_at FROM entry WHERE id = :id"), {"id": written.entry_id}
        )
    async with person_transaction(app_engine, person) as scope:
        assert await void_entry(scope, written.entry_id) is VoidOutcome.ALREADY_VOIDED
    async with owner_engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT voided_at FROM entry WHERE id = :id"), {"id": written.entry_id})
            == first_voided_at
        )


async def test_undo_of_an_unknown_entry_says_so(app_engine: AsyncEngine, person: int) -> None:
    async with person_transaction(app_engine, person) as scope:
        assert await void_entry(scope, -1) is VoidOutcome.NOT_FOUND


async def test_changing_the_category_moves_the_entry_and_remembers_the_word(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, person: int
) -> None:
    written = await add_entry(app_engine, person, spending(30_000, "Lainnya", "martabak manis"))
    async with person_transaction(app_engine, person) as scope:
        assert await change_entry_category(scope, written.entry_id, "makan") is CategoryChange.CHANGED
    assert (await stored_entries(owner_engine, person))[0][2] == "Makan"
    next_entry = await add_entry(app_engine, person, spending(20_000, "Lainnya", "beli martabak"))
    assert next_entry.category == "Makan"


async def test_a_remembered_word_can_be_changed_again(app_engine: AsyncEngine, person: int) -> None:
    written = await add_entry(app_engine, person, spending(30_000, "Lainnya", "martabak"))
    async with person_transaction(app_engine, person) as scope:
        await change_entry_category(scope, written.entry_id, "Makan")
        await change_entry_category(scope, written.entry_id, "Hiburan")
    assert (await add_entry(app_engine, person, spending(1_000, "Lainnya", "martabak"))).category == "Hiburan"


async def test_a_remembered_word_belongs_to_one_person(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, person: int
) -> None:
    written = await add_entry(app_engine, person, spending(30_000, "Lainnya", "martabak"))
    async with person_transaction(app_engine, person) as scope:
        await change_entry_category(scope, written.entry_id, "Makan")
    other_person = await new_person(owner_engine)
    assert (await add_entry(app_engine, other_person, spending(1_000, "Lainnya", "martabak"))).category == "Lainnya"


async def test_a_remembered_spending_word_does_not_recategorise_income(app_engine: AsyncEngine, person: int) -> None:
    written = await add_entry(app_engine, person, spending(30_000, "Lainnya", "bonus kopi"))
    async with person_transaction(app_engine, person) as scope:
        await change_entry_category(scope, written.entry_id, "Makan")
    assert (await add_entry(app_engine, person, income(1_000, "Gaji", "bonus"))).category == "Gaji"


async def test_an_unknown_category_changes_nothing(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, person: int
) -> None:
    written = await add_entry(app_engine, person, spending(30_000, "Makan"))
    async with person_transaction(app_engine, person) as scope:
        assert await change_entry_category(scope, written.entry_id, "Liburan") is CategoryChange.UNKNOWN_CATEGORY
    assert (await stored_entries(owner_engine, person))[0][2] == "Makan"


async def test_a_spending_entry_cannot_move_to_an_income_category(app_engine: AsyncEngine, person: int) -> None:
    written = await add_entry(app_engine, person, spending(30_000))
    async with person_transaction(app_engine, person) as scope:
        assert await change_entry_category(scope, written.entry_id, "Gaji") is CategoryChange.UNKNOWN_CATEGORY


async def test_a_voided_entry_cannot_be_recategorised(app_engine: AsyncEngine, person: int) -> None:
    written = await add_entry(app_engine, person, spending(30_000))
    async with person_transaction(app_engine, person) as scope:
        await void_entry(scope, written.entry_id)
        assert await change_entry_category(scope, written.entry_id, "Hiburan") is CategoryChange.ENTRY_NOT_FOUND


async def test_a_description_with_no_word_changes_the_category_and_remembers_nothing(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, person: int
) -> None:
    written = await add_entry(app_engine, person, spending(30_000, "Lainnya", ""))
    async with person_transaction(app_engine, person) as scope:
        assert await change_entry_category(scope, written.entry_id, "Makan") is CategoryChange.CHANGED
    async with owner_engine.connect() as connection:
        remembered = await connection.scalar(
            text("SELECT count(*) FROM category_keyword WHERE person_id = :id"), {"id": person}
        )
    assert remembered == 0
