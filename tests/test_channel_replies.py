"""The bot's words, without a database."""

import pytest

from tracker.channel.replies import (
    _REASON_TEXT,  # pyright: ignore[reportPrivateUsage]
    CONSENT_DECLINED,
    format_rupiah,
)
from tracker.parsing.amount import AmbiguityReason


@pytest.mark.parametrize(
    ("amount", "formatted"),
    [(0, "Rp0"), (500, "Rp500"), (1_000, "Rp1.000"), (1_500_000, "Rp1.500.000"), (1_000_000_000, "Rp1.000.000.000")],
)
def test_rupiah_uses_indonesian_grouping(amount: int, formatted: str) -> None:
    assert format_rupiah(amount) == formatted


def test_every_ambiguity_reason_has_something_to_say() -> None:
    assert set(_REASON_TEXT) == set(AmbiguityReason)


def test_declining_says_exactly_what_is_kept_and_who_removes_it() -> None:
    """L9: a decline keeps the invitation, with the WhatsApp id, so the reply can't say nothing was kept."""
    assert "Tidak ada pesanmu yang disimpan" not in CONSENT_DECLINED
    assert "undangan" in CONSENT_DECLINED
    assert "ID WhatsApp" in CONSENT_DECLINED
    assert "Arthur" in CONSENT_DECLINED
