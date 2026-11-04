"""Deleting moves from the app to an owner job (M1, ADR-023).

The app chooses whose scope it acts in, so a taken-over app could call the delete
function for anyone. Now the app can only mark a confirmed request, which locks that
person out at once, and `tracker deletions-run`, under the owner login, deletes at most
two people a run (tracker/ledger/deletions.py).

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE person ADD COLUMN deletion_confirmed_at timestamptz")
    op.execute("GRANT UPDATE (deletion_confirmed_at) ON person TO tracker_app")
    op.execute("REVOKE EXECUTE ON FUNCTION tracker_delete_person_in_scope() FROM tracker_app")
    # The same function as 0005, except that a confirmed deletion makes the person a stranger.
    op.execute("""
        CREATE OR REPLACE FUNCTION tracker_resolve_sender(sender_id text, sender_phone text) RETURNS bigint
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $$
            DECLARE
                resolved_id bigint;
            BEGIN
                SELECT id INTO resolved_id FROM public.person
                    WHERE whatsapp_id = sender_id AND suspended_at IS NULL AND deletion_confirmed_at IS NULL;
                IF resolved_id IS NULL AND sender_phone IS NOT NULL THEN
                    UPDATE public.person SET whatsapp_id = sender_id, phone = NULL
                        WHERE phone = sender_phone AND whatsapp_id IS NULL AND suspended_at IS NULL
                        RETURNING id INTO resolved_id;
                END IF;
                IF resolved_id IS NULL AND sender_phone IS NOT NULL THEN
                    UPDATE public.person SET whatsapp_id = sender_id
                        WHERE whatsapp_id = sender_phone || '@c.us' AND suspended_at IS NULL
                            AND deletion_confirmed_at IS NULL
                        RETURNING id INTO resolved_id;
                END IF;
                IF resolved_id IS NULL THEN
                    SELECT id INTO resolved_id FROM public.person
                        WHERE whatsapp_id = sender_id AND suspended_at IS NULL AND deletion_confirmed_at IS NULL;
                END IF;
                RETURN resolved_id;
            END
            $$
    """)


def downgrade() -> None:
    raise NotImplementedError("the ledger only moves forward: restore from a backup instead")
