"""WAHA replaces Meta's Cloud API (ADR-022).

- A BSUID doesn't exist on WAHA, so `person.bsuid` becomes `whatsapp_id`: a `@lid` when
  WAHA gives one, otherwise `@c.us`.
- Reply buttons don't work, so consent is typed. `notice_sent_version` records which
  notice a person was sent, so `setuju` can't agree to one they never saw.
- WAHA has no number-change message, so that function goes.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE person RENAME COLUMN bsuid TO whatsapp_id")
    op.execute("ALTER TABLE person ADD COLUMN notice_sent_version text")
    op.execute("DROP FUNCTION tracker_change_bsuid(text, text)")
    op.execute("DROP FUNCTION tracker_resolve_sender(text, text)")
    op.execute("""
        CREATE FUNCTION tracker_resolve_sender(sender_id text, sender_phone text) RETURNS bigint
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $$
            DECLARE
                resolved_id bigint;
            BEGIN
                SELECT id INTO resolved_id FROM public.person
                    WHERE whatsapp_id = sender_id AND suspended_at IS NULL;
                IF resolved_id IS NULL AND sender_phone IS NOT NULL THEN
                    UPDATE public.person SET whatsapp_id = sender_id, phone = NULL
                        WHERE phone = sender_phone AND whatsapp_id IS NULL AND suspended_at IS NULL
                        RETURNING id INTO resolved_id;
                END IF;
                RETURN resolved_id;
            END
            $$
    """)
    op.execute("REVOKE ALL ON FUNCTION tracker_resolve_sender(text, text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION tracker_resolve_sender(text, text) TO tracker_app")
    op.execute("GRANT UPDATE (notice_sent_version) ON person TO tracker_app")


def downgrade() -> None:
    raise NotImplementedError("the ledger only moves forward: restore from a backup instead")
