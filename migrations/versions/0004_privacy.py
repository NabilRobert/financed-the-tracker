"""Leaving, and what's recorded about a message.

- tracker_app can delete nothing, so `hapus data saya` goes through one function that
  deletes only the person in scope, and takes no argument that could name anyone else.
- `deleted_person` keeps the ids of people who left, and nothing else about them, so a
  deletion can be re-applied after a backup is restored (PR7).
- `outcome` records what became of a message, with no text, so misses can be counted.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE inbound_message
            ADD COLUMN outcome text
                CHECK (outcome IN ('entry', 'question', 'answer', 'command', 'unparsed', 'refused', 'not_text'))
    """)
    op.execute("GRANT UPDATE (outcome) ON inbound_message TO tracker_app")
    op.execute("ALTER TABLE person ADD COLUMN deletion_requested_at timestamptz")
    op.execute("GRANT UPDATE (deletion_requested_at) ON person TO tracker_app")
    op.execute("""
        CREATE TABLE deleted_person (
            person_id bigint PRIMARY KEY,
            deleted_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    # Row-level security with no policy: only the function below writes here.
    op.execute("ALTER TABLE deleted_person ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE FUNCTION tracker_delete_person_in_scope() RETURNS void
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $$
            DECLARE
                person_in_scope bigint := public.tracker_current_person_id();
            BEGIN
                IF person_in_scope IS NULL THEN
                    RAISE EXCEPTION 'no person in scope';
                END IF;
                DELETE FROM public.budget_warning WHERE person_id = person_in_scope;
                DELETE FROM public.budget WHERE person_id = person_in_scope;
                DELETE FROM public.category_keyword WHERE person_id = person_in_scope;
                DELETE FROM public.pending_entry WHERE person_id = person_in_scope;
                DELETE FROM public.entry WHERE person_id = person_in_scope;
                DELETE FROM public.inbound_message WHERE person_id = person_in_scope;
                DELETE FROM public.category WHERE person_id = person_in_scope;
                DELETE FROM public.person WHERE id = person_in_scope;
                INSERT INTO public.deleted_person (person_id) VALUES (person_in_scope)
                    ON CONFLICT (person_id) DO NOTHING;
            END
            $$
    """)
    op.execute("REVOKE ALL ON FUNCTION tracker_delete_person_in_scope() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION tracker_delete_person_in_scope() TO tracker_app")


def downgrade() -> None:
    raise NotImplementedError("the ledger only moves forward: restore from a backup instead")
