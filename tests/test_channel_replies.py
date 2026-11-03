"""The bot's words, without a database."""

import pytest

from tracker.channel.replies import _REASON_TEXT, format_rupiah  # pyright: ignore[reportPrivateUsage]
from tracker.parsing.amount import AmbiguityReason


@pytest.mark.parametrize(
    ("amount", "formatted"),
    [(0, "Rp0"), (500, "Rp500"), (1_000, "Rp1.000"), (1_500_000, "Rp1.500.000"), (1_000_000_000, "Rp1.000.000.000")],
)
def test_rupiah_uses_indonesian_grouping(amount: int, formatted: str) -> None:
    assert format_rupiah(amount) == formatted


def test_every_ambiguity_reason_has_something_to_say() -> None:
    assert set(_REASON_TEXT) == set(AmbiguityReason)
