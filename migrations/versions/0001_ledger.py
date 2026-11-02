"""The ledger: people, messages, categories, entries and budgets.

Three rules are enforced here rather than in the app, so a bug can't break them:

- Isolation (ADR-009). Row-level security limits tracker_app to one person per
  transaction. Foreign keys ignore row-level security, so every link between
  people's tables carries person_id too: an entry can't point at someone else's
  category.
- Money (L1, L2). Whole rupiah in a BIGINT, above 0 and within the entry limit.
- The ledger only grows (L3). tracker_app has no DELETE anywhere, and on an entry
  it can update only voided_at and category_id. The amount can't be edited.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

PERSON_SCOPED_TABLES = ("inbound_message", "category", "category_keyword", "entry", "budget", "budget_warning")
APP_GRANTS = (
    "SELECT ON person, category",
    "SELECT, INSERT ON inbound_message, budget_warning",
    "SELECT, INSERT, UPDATE (category_id) ON category_keyword",
    "SELECT, INSERT, UPDATE (voided_at, category_id) ON entry",
    "SELECT, INSERT, UPDATE (limit_rupiah) ON budget",
)


def upgrade() -> None:
    op.execute("""
        -- NULLIF because a setting that a past transaction set reads as '' here, not NULL,
        -- and a reused connection must see no rows rather than an error.
        CREATE FUNCTION tracker_current_person_id() RETURNS bigint
            LANGUAGE sql STABLE
            AS $$ SELECT NULLIF(current_setting('tracker.person_id', true), '')::bigint $$
    """)
    op.execute("""
        CREATE TABLE person (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            bsuid text NOT NULL UNIQUE,
            phone text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE inbound_message (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            person_id bigint NOT NULL REFERENCES person (id),
            meta_message_id text NOT NULL UNIQUE,
            received_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id, person_id)
        )
    """)
    op.execute("""
        CREATE TABLE category (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            person_id bigint NOT NULL REFERENCES person (id),
            name text NOT NULL,
            direction text NOT NULL CHECK (direction IN ('spending', 'income')),
            UNIQUE (person_id, name),
            UNIQUE (id, person_id)
        )
    """)
    op.execute("""
        CREATE TABLE category_keyword (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            person_id bigint NOT NULL REFERENCES person (id),
            word text NOT NULL,
            category_id bigint NOT NULL,
            UNIQUE (person_id, word),
            FOREIGN KEY (category_id, person_id) REFERENCES category (id, person_id)
        )
    """)
    op.execute("""
        CREATE TABLE entry (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            person_id bigint NOT NULL REFERENCES person (id),
            inbound_message_id bigint NOT NULL UNIQUE,
            direction text NOT NULL CHECK (direction IN ('spending', 'income')),
            amount_rupiah bigint NOT NULL CHECK (amount_rupiah > 0 AND amount_rupiah <= 1000000000),
            category_id bigint NOT NULL,
            description text NOT NULL,
            occurred_on date NOT NULL,
            source text NOT NULL CHECK (source IN ('rules', 'model')),
            voided_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            FOREIGN KEY (inbound_message_id, person_id) REFERENCES inbound_message (id, person_id),
            FOREIGN KEY (category_id, person_id) REFERENCES category (id, person_id)
        )
    """)
    op.execute("CREATE INDEX entry_person_occurred_on ON entry (person_id, occurred_on)")
    op.execute("""
        CREATE TABLE budget (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            person_id bigint NOT NULL REFERENCES person (id),
            category_id bigint NOT NULL,
            limit_rupiah bigint NOT NULL CHECK (limit_rupiah > 0 AND limit_rupiah <= 1000000000),
            UNIQUE (person_id, category_id),
            FOREIGN KEY (category_id, person_id) REFERENCES category (id, person_id)
        )
    """)
    op.execute("""
        CREATE TABLE budget_warning (
            person_id bigint NOT NULL REFERENCES person (id),
            category_id bigint NOT NULL,
            month date NOT NULL CHECK (EXTRACT(DAY FROM month) = 1),
            threshold_percent smallint NOT NULL CHECK (threshold_percent IN (80, 100)),
            PRIMARY KEY (person_id, category_id, month, threshold_percent),
            FOREIGN KEY (category_id, person_id) REFERENCES category (id, person_id)
        )
    """)
    for table in PERSON_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY own_rows ON {table}
                USING (person_id = tracker_current_person_id())
                WITH CHECK (person_id = tracker_current_person_id())
        """)
    op.execute("ALTER TABLE person ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY own_row ON person USING (id = tracker_current_person_id())")
    op.execute("""
        -- A webhook names a person by BSUID before any person is in scope. This answers
        -- that one question and no other, so tracker_app never reads the person table
        -- unscoped. It runs as the owner, with a fixed search_path so nothing can stand
        -- in for the table.
        CREATE FUNCTION tracker_person_id_for_bsuid(wanted_bsuid text) RETURNS bigint
            LANGUAGE sql STABLE SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $$ SELECT id FROM public.person WHERE bsuid = wanted_bsuid $$
    """)
    op.execute("REVOKE ALL ON FUNCTION tracker_person_id_for_bsuid(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION tracker_person_id_for_bsuid(text) TO tracker_app")
    for grant in APP_GRANTS:
        op.execute(f"GRANT {grant} TO tracker_app")


def downgrade() -> None:
    raise NotImplementedError("the ledger only moves forward: restore from a backup instead")
