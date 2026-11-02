"""What the WhatsApp channel needs: joining by number, consent, suspension, pending entries.

- A person starts as a phone number on the allow-list, because Arthur adds people
  before Meta has told us their BSUID. The first message links the two, and the
  number is dropped (T21): after that the BSUID is the only key, so a recycled
  number reaches nothing (T12).
- A suspended person resolves to nobody, so one check stops every read and write (I8).
- Strangers leave a daily count and nothing else (I3).

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE person ALTER COLUMN bsuid DROP NOT NULL")
    op.execute("""
        ALTER TABLE person
            ADD COLUMN consented_at timestamptz,
            ADD COLUMN consent_notice_version text,
            ADD COLUMN suspended_at timestamptz,
            ADD CONSTRAINT person_has_an_identity CHECK (bsuid IS NOT NULL OR phone IS NOT NULL),
            ADD CONSTRAINT consent_names_its_notice CHECK ((consented_at IS NULL) = (consent_notice_version IS NULL)),
            ADD CONSTRAINT person_phone_is_unique UNIQUE (phone)
    """)
    op.execute("DROP FUNCTION tracker_person_id_for_bsuid(text)")
    op.execute("""
        CREATE FUNCTION tracker_resolve_sender(sender_bsuid text, sender_phone text) RETURNS bigint
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $$
            DECLARE
                resolved_id bigint;
            BEGIN
                SELECT id INTO resolved_id FROM public.person
                    WHERE bsuid = sender_bsuid AND suspended_at IS NULL;
                IF resolved_id IS NULL AND sender_phone IS NOT NULL THEN
                    UPDATE public.person SET bsuid = sender_bsuid, phone = NULL
                        WHERE phone = sender_phone AND bsuid IS NULL AND suspended_at IS NULL
                        RETURNING id INTO resolved_id;
                END IF;
                RETURN resolved_id;
            END
            $$
    """)
    op.execute("""
        CREATE TABLE unknown_sender_count (
            day date PRIMARY KEY,
            senders bigint NOT NULL
        )
    """)
    # Row-level security with no policy: tracker_app reaches this only through the function.
    op.execute("ALTER TABLE unknown_sender_count ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE FUNCTION tracker_count_unknown_sender() RETURNS void
            LANGUAGE sql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $$
                INSERT INTO public.unknown_sender_count (day, senders)
                VALUES ((now() AT TIME ZONE 'Asia/Jakarta')::date, 1)
                ON CONFLICT (day) DO UPDATE SET senders = public.unknown_sender_count.senders + 1
            $$
    """)
    op.execute("""
        CREATE FUNCTION tracker_change_bsuid(old_bsuid text, new_bsuid text) RETURNS boolean
            LANGUAGE sql SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $$
                WITH moved AS (
                    UPDATE public.person SET bsuid = new_bsuid
                    WHERE bsuid = old_bsuid
                      AND NOT EXISTS (SELECT 1 FROM public.person taken WHERE taken.bsuid = new_bsuid)
                    RETURNING id
                )
                SELECT EXISTS (SELECT 1 FROM moved)
            $$
    """)
    for function in (
        "tracker_resolve_sender(text, text)",
        "tracker_count_unknown_sender()",
        "tracker_change_bsuid(text, text)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO tracker_app")
    op.execute("""
        CREATE TABLE pending_entry (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            person_id bigint NOT NULL REFERENCES person (id),
            inbound_message_id bigint NOT NULL UNIQUE,
            direction text NOT NULL CHECK (direction IN ('spending', 'income')),
            first_amount_rupiah bigint NOT NULL
                CHECK (first_amount_rupiah > 0 AND first_amount_rupiah <= 1000000000),
            second_amount_rupiah bigint
                CHECK (second_amount_rupiah > 0 AND second_amount_rupiah <= 1000000000),
            category_id bigint NOT NULL,
            description text NOT NULL,
            occurred_on date NOT NULL,
            expires_at timestamptz NOT NULL,
            resolved_at timestamptz,
            FOREIGN KEY (inbound_message_id, person_id) REFERENCES inbound_message (id, person_id),
            FOREIGN KEY (category_id, person_id) REFERENCES category (id, person_id)
        )
    """)
    op.execute("ALTER TABLE pending_entry ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY own_rows ON pending_entry
            USING (person_id = tracker_current_person_id())
            WITH CHECK (person_id = tracker_current_person_id())
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE (resolved_at) ON pending_entry TO tracker_app")
    op.execute("GRANT UPDATE (consented_at, consent_notice_version) ON person TO tracker_app")


def downgrade() -> None:
    raise NotImplementedError("the ledger only moves forward: restore from a backup instead")
