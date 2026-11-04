"""A person linked by number moves onto their LID when WAHA learns it (M2, T12).

Some people are linked as `<number>@c.us`, because WAHA didn't know their LID at first
contact. Their later messages arrive as the LID, which matched nobody, so they went
silent: no logging, no `ekspor`, no `hapus data saya`. Now a LID whose number matches
such a person takes their place. That hands the ledger to whoever holds the number at
that moment, which Arthur accepted in writing on 2026-11-04 (threat model, T12). A
person already on a LID is never moved, so a recycled number still reaches nothing.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION tracker_resolve_sender(sender_id text, sender_phone text) RETURNS bigint
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
                IF resolved_id IS NULL AND sender_phone IS NOT NULL THEN
                    UPDATE public.person SET whatsapp_id = sender_id
                        WHERE whatsapp_id = sender_phone || '@c.us' AND suspended_at IS NULL
                        RETURNING id INTO resolved_id;
                END IF;
                -- A second message that waited on another's move finds the row already on
                -- the LID, so the move matches nothing. Look again: this statement sees it.
                IF resolved_id IS NULL THEN
                    SELECT id INTO resolved_id FROM public.person
                        WHERE whatsapp_id = sender_id AND suspended_at IS NULL;
                END IF;
                RETURN resolved_id;
            END
            $$
    """)


def downgrade() -> None:
    raise NotImplementedError("the ledger only moves forward: restore from a backup instead")
