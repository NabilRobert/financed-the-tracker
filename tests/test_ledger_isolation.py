"""One person can never reach another's data (G4, I5, ADR-009).

There are two layers, and each is tested without the other. The raw-SQL tests
leave out every WHERE, so only row-level security stands in the way. The
owner-login tests run the app's own functions where row-level security doesn't
apply, so only the code's filters do.
"""

from collections.abc import Awaitable, Callable

import pytest
from ledger_helpers import A_DAY, add_entry, new_person, spending
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tracker.ledger.budgets import BudgetUse, list_budgets, set_budget
from tracker.ledger.categories import CategoryChange, change_entry_category, resolve_category
from tracker.ledger.entries import VoidOutcome, claim_message, void_entry
from tracker.ledger.pending import NewPendingEntry, cancel_pending_entry, confirm_pending_entry, create_pending_entry
from tracker.ledger.people import resolve_sender
from tracker.ledger.scope import PersonScope, person_transaction
from tracker.ledger.summaries import month_period, summarise
from tracker.parsing.message import Direction

PERSON_TABLES = [
    "person",
    "inbound_message",
    "category",
    "category_keyword",
    "entry",
    "pending_entry",
    "budget",
    "budget_warning",
]
TABLES_WITH_PERSON_ID = PERSON_TABLES[1:]


@pytest.fixture
async def alice(owner_engine: AsyncEngine, app_engine: AsyncEngine) -> int:
    """A person with a row in every table."""
    person_id = await new_person(owner_engine)
    async with person_transaction(app_engine, person_id) as scope:
        await set_budget(scope, "Makan", 10_000)
    written = await add_entry(app_engine, person_id, spending(9_000))
    async with person_transaction(app_engine, person_id) as scope:
        await change_entry_category(scope, written.entry_id, "Hiburan")
        await change_entry_category(scope, written.entry_id, "Makan")
        inbound_message_id = await claim_message(scope, f"wamid.pending-{person_id}")
        assert inbound_message_id is not None
        category = await resolve_category(scope, "", Direction.SPENDING, "Makan")
        pending = NewPendingEntry((25, 25_000), Direction.SPENDING, category, "kopi", A_DAY)
        await create_pending_entry(scope, inbound_message_id, pending)
    return person_id


@pytest.fixture
async def bob(owner_engine: AsyncEngine) -> int:
    return await new_person(owner_engine)


async def count_rows(owner_engine: AsyncEngine, table: str, person_id: int) -> int:
    column = "id" if table == "person" else "person_id"
    async with owner_engine.connect() as connection:
        count = await connection.scalar(text(f"SELECT count(*) FROM {table} WHERE {column} = :id"), {"id": person_id})
    return int(count or 0)


async def entry_id_of(owner_engine: AsyncEngine, person_id: int) -> int:
    async with owner_engine.connect() as connection:
        entry_id = await connection.scalar(
            text("SELECT id FROM entry WHERE person_id = :id LIMIT 1"), {"id": person_id}
        )
    assert entry_id is not None
    return int(entry_id)


async def test_the_app_login_is_bound_by_row_level_security(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as connection:
        row = (
            await connection.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"))
        ).one()
        owned_tables = await connection.scalar(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user")
        )
    assert (row.rolsuper, row.rolbypassrls, owned_tables) == (False, False, 0)


async def test_every_table_in_the_schema_has_row_level_security(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text("""
                SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
            """)
        )
    assert [row.relname for row in rows] == ["alembic_version"]


@pytest.mark.parametrize("table", PERSON_TABLES)
async def test_the_alice_fixture_covers_the_table(owner_engine: AsyncEngine, alice: int, table: str) -> None:
    assert await count_rows(owner_engine, table, alice) > 0


@pytest.mark.parametrize("table", PERSON_TABLES)
async def test_with_no_person_in_scope_every_table_is_empty(app_engine: AsyncEngine, alice: int, table: str) -> None:
    assert alice
    async with app_engine.connect() as connection:
        assert await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0


@pytest.mark.parametrize("table", TABLES_WITH_PERSON_ID)
async def test_a_query_with_no_where_returns_only_the_person_in_scope(
    app_engine: AsyncEngine, alice: int, bob: int, table: str
) -> None:
    async with person_transaction(app_engine, bob) as scope:
        foreign_rows = await scope.connection.scalar(
            text(f"SELECT count(*) FROM {table} WHERE person_id <> :bob"), {"bob": bob}
        )
    assert alice
    assert foreign_rows == 0


async def test_the_person_table_shows_only_your_own_row(app_engine: AsyncEngine, alice: int, bob: int) -> None:
    async with person_transaction(app_engine, bob) as scope:
        rows = await scope.connection.execute(text("SELECT id FROM person"))
    assert alice
    assert [row.id for row in rows] == [bob]


async def test_a_brand_new_connection_sees_no_rows_and_no_error(app_engine: AsyncEngine, alice: int) -> None:
    """A setting never set reads differently from one a past transaction set: both must be safe."""
    assert alice
    fresh_engine = create_async_engine(app_engine.url, poolclass=NullPool)
    try:
        async with fresh_engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM entry")) == 0
    finally:
        await fresh_engine.dispose()


async def test_the_scope_ends_with_the_transaction_on_a_reused_connection(app_engine: AsyncEngine, alice: int) -> None:
    async with app_engine.connect() as connection:
        async with connection.begin():
            await connection.execute(text("SELECT set_config('tracker.person_id', :id, true)"), {"id": str(alice)})
            assert await connection.scalar(text("SELECT count(*) FROM entry")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM entry")) == 0


async def test_a_raw_update_cannot_reach_another_person(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, alice: int, bob: int
) -> None:
    entry_id = await entry_id_of(owner_engine, alice)
    async with person_transaction(app_engine, bob) as scope:
        result = await scope.connection.execute(
            text("UPDATE entry SET voided_at = now() WHERE id = :id"), {"id": entry_id}
        )
    assert result.rowcount == 0


async def test_a_raw_insert_cannot_write_as_another_person(app_engine: AsyncEngine, alice: int, bob: int) -> None:
    with pytest.raises(DBAPIError, match="row-level security"):
        async with person_transaction(app_engine, bob) as scope:
            await scope.connection.execute(
                text("INSERT INTO inbound_message (person_id, meta_message_id) VALUES (:id, 'forged')"), {"id": alice}
            )


async def test_an_entry_cannot_point_at_another_persons_category(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, alice: int, bob: int
) -> None:
    """Foreign keys ignore row-level security, so the link itself must carry person_id."""
    async with owner_engine.connect() as connection:
        category_of_alice = await connection.scalar(
            text("SELECT id FROM category WHERE person_id = :id LIMIT 1"), {"id": alice}
        )
    with pytest.raises(IntegrityError, match="foreign key"):
        async with person_transaction(app_engine, bob) as scope:
            inbound_id = await scope.connection.scalar(
                text("INSERT INTO inbound_message (person_id, meta_message_id) VALUES (:bob, :meta) RETURNING id"),
                {"bob": bob, "meta": f"cross-{bob}"},
            )
            await scope.connection.execute(
                text("""
                    INSERT INTO entry (person_id, inbound_message_id, direction, amount_rupiah, category_id,
                                       description, occurred_on, source)
                    VALUES (:bob, :inbound, 'spending', 1000, :category, 'x', CURRENT_DATE, 'rules')
                """),
                {"bob": bob, "inbound": inbound_id, "category": category_of_alice},
            )


async def test_undo_cannot_reach_another_persons_entry(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, alice: int, bob: int
) -> None:
    entry_id = await entry_id_of(owner_engine, alice)
    async with person_transaction(app_engine, bob) as scope:
        assert await void_entry(scope, entry_id) is VoidOutcome.NOT_FOUND
    async with person_transaction(app_engine, alice) as scope:
        assert await void_entry(scope, entry_id) is VoidOutcome.VOIDED


async def test_the_code_filters_undo_even_where_row_level_security_does_not_apply(
    owner_engine: AsyncEngine, alice: int, bob: int
) -> None:
    entry_id = await entry_id_of(owner_engine, alice)
    async with owner_engine.begin() as connection:
        assert await void_entry(PersonScope(connection, bob), entry_id) is VoidOutcome.NOT_FOUND


async def test_the_code_filters_recategorising_even_where_row_level_security_does_not_apply(
    owner_engine: AsyncEngine, alice: int, bob: int
) -> None:
    entry_id = await entry_id_of(owner_engine, alice)
    async with owner_engine.begin() as connection:
        change = await change_entry_category(PersonScope(connection, bob), entry_id, "Hiburan")
    assert change is CategoryChange.ENTRY_NOT_FOUND


async def test_the_code_filters_remembered_words_even_where_row_level_security_does_not_apply(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, bob: int
) -> None:
    teacher = await new_person(owner_engine)
    written = await add_entry(app_engine, teacher, spending(1_000, "Lainnya", "martabak"))
    async with person_transaction(app_engine, teacher) as scope:
        await change_entry_category(scope, written.entry_id, "Makan")
    async with owner_engine.begin() as connection:
        category = await resolve_category(PersonScope(connection, bob), "martabak", Direction.SPENDING, "Lainnya")
    assert category.name == "Lainnya"


async def test_the_code_finds_only_your_own_category_even_where_row_level_security_does_not_apply(
    owner_engine: AsyncEngine, alice: int, bob: int
) -> None:
    assert alice
    async with owner_engine.begin() as connection:
        category = await resolve_category(PersonScope(connection, bob), "", Direction.SPENDING, "Makan")
        owner_of_category = await connection.scalar(
            text("SELECT person_id FROM category WHERE id = :id"), {"id": category.id}
        )
    assert owner_of_category == bob


async def test_the_code_sets_a_budget_on_your_own_category_even_where_row_level_security_does_not_apply(
    owner_engine: AsyncEngine, alice: int, bob: int
) -> None:
    assert alice
    async with owner_engine.begin() as connection:
        await set_budget(PersonScope(connection, bob), "Makan", 5_000)
        assert await list_budgets(PersonScope(connection, bob), A_DAY) == [BudgetUse("Makan", 5_000, 0)]


async def test_a_budget_counts_only_its_owners_entries_even_where_row_level_security_does_not_apply(
    owner_engine: AsyncEngine, alice: int
) -> None:
    async with owner_engine.begin() as connection:
        uses = await list_budgets(PersonScope(connection, alice), A_DAY)
    assert [use.used_rupiah for use in uses] == [9_000]


async def test_every_link_between_peoples_tables_carries_person_id(owner_engine: AsyncEngine) -> None:
    """Foreign keys ignore row-level security, so a link without person_id could cross people."""
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text("""
                SELECT c.conname FROM pg_constraint c
                WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
                  AND c.confrelid <> 'person'::regclass
                  AND NOT EXISTS (
                      SELECT 1 FROM unnest(c.conkey) AS key_column
                      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = key_column
                      WHERE a.attname = 'person_id'
                  )
            """)
        )
    assert list(rows) == []


@pytest.mark.parametrize("function", ["tracker_resolve_sender", "tracker_count_unknown_sender", "tracker_change_bsuid"])
async def test_each_owner_function_is_for_the_app_alone_and_cannot_be_redirected(
    owner_engine: AsyncEngine, function: str
) -> None:
    async with owner_engine.connect() as connection:
        row = (
            await connection.execute(
                text("""
                    SELECT p.proconfig, p.prosecdef,
                           has_function_privilege('tracker_app', p.oid, 'EXECUTE') AS app_may_call,
                           EXISTS (SELECT 1 FROM aclexplode(p.proacl) acl WHERE acl.grantee = 0) AS public_may_call
                    FROM pg_proc p WHERE p.proname = :function
                """),
                {"function": function},
            )
        ).one()
    assert row.proconfig == ["search_path=pg_catalog, public"]
    assert (row.prosecdef, row.app_may_call, row.public_may_call) == (True, True, False)


async def test_the_app_can_only_touch_its_own_consent_on_the_person_table(
    app_engine: AsyncEngine, alice: int, bob: int
) -> None:
    async with person_transaction(app_engine, bob) as scope:
        result = await scope.connection.execute(
            text("UPDATE person SET consented_at = now(), consent_notice_version = 'x' WHERE id = :id"), {"id": alice}
        )
    assert result.rowcount == 0
    with pytest.raises(DBAPIError, match="permission denied"):
        async with person_transaction(app_engine, bob) as scope:
            await scope.connection.execute(text("UPDATE person SET suspended_at = NULL"))


async def test_the_app_cannot_read_the_migration_history(app_engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_engine.connect() as connection:
            await connection.execute(text("SELECT * FROM alembic_version"))


async def test_the_code_moves_an_entry_to_your_own_category_even_where_row_level_security_does_not_apply(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, alice: int, bob: int
) -> None:
    """Alice's categories come first in the table, so an unfiltered lookup would find hers."""
    assert alice
    written = await add_entry(app_engine, bob, spending(1_000))
    async with owner_engine.begin() as connection:
        change = await change_entry_category(PersonScope(connection, bob), written.entry_id, "Hiburan")
    assert change is CategoryChange.CHANGED


async def _summary_rows(scope: PersonScope) -> int:
    return len((await summarise(scope, month_period(A_DAY.year, A_DAY.month))).categories)


async def _budget_rows(scope: PersonScope) -> int:
    return len(await list_budgets(scope, A_DAY))


@pytest.mark.parametrize("read", [_summary_rows, _budget_rows])
async def test_the_code_filters_reads_even_where_row_level_security_does_not_apply(
    owner_engine: AsyncEngine, alice: int, bob: int, read: Callable[[PersonScope], Awaitable[int]]
) -> None:
    async with owner_engine.begin() as connection:
        assert await read(PersonScope(connection, alice)) == 1
        assert await read(PersonScope(connection, bob)) == 0


async def test_a_person_is_found_by_bsuid_without_reading_the_table(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, bob: int
) -> None:
    async with owner_engine.connect() as connection:
        bsuid = await connection.scalar(text("SELECT bsuid FROM person WHERE id = :id"), {"id": bob})
    assert await resolve_sender(app_engine, str(bsuid), None) == bob
    assert await resolve_sender(app_engine, "ID.nobody", None) is None


async def _pending_id_of(owner_engine: AsyncEngine, person_id: int) -> int:
    async with owner_engine.connect() as connection:
        pending_id = await connection.scalar(
            text("SELECT id FROM pending_entry WHERE person_id = :id LIMIT 1"), {"id": person_id}
        )
    assert pending_id is not None
    return int(pending_id)


async def test_a_tap_cannot_confirm_or_cancel_another_persons_pending_entry(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, alice: int, bob: int
) -> None:
    pending_id = await _pending_id_of(owner_engine, alice)
    async with person_transaction(app_engine, bob) as scope:
        assert await confirm_pending_entry(scope, pending_id, 0) is None
        assert not await cancel_pending_entry(scope, pending_id)


async def test_the_code_filters_pending_entries_even_where_row_level_security_does_not_apply(
    owner_engine: AsyncEngine, alice: int, bob: int
) -> None:
    pending_id = await _pending_id_of(owner_engine, alice)
    async with owner_engine.begin() as connection:
        scope = PersonScope(connection, bob)
        assert await confirm_pending_entry(scope, pending_id, 0) is None
        assert not await cancel_pending_entry(scope, pending_id)


@pytest.mark.parametrize("table", PERSON_TABLES)
async def test_the_app_can_delete_nothing(app_engine: AsyncEngine, alice: int, table: str) -> None:
    assert alice
    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_engine.begin() as connection:
            await connection.execute(text(f"DELETE FROM {table}"))


@pytest.mark.parametrize(
    "assignment",
    ["amount_rupiah = 1", "direction = 'income'", "person_id = 1", "description = 'x'", "occurred_on = now()"],
)
async def test_the_app_cannot_edit_what_an_entry_says(app_engine: AsyncEngine, alice: int, assignment: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with person_transaction(app_engine, alice) as scope:
            await scope.connection.execute(text(f"UPDATE entry SET {assignment}"))
