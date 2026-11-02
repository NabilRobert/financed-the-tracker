"""The bot's words and its button payloads, without a database."""

import pytest

from tracker.channel.buttons import (
    ButtonAction,
    ButtonPress,
    agree_payload,
    cancel_payload,
    confirm_payload,
    read_payload,
    undo_payload,
)
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


def test_payloads_read_back_to_what_was_sent() -> None:
    assert read_payload(confirm_payload(7, 1)) == ButtonPress(ButtonAction.CONFIRM, 7, 1)
    assert read_payload(cancel_payload(7)) == ButtonPress(ButtonAction.CANCEL, 7)
    assert read_payload(undo_payload(7)) == ButtonPress(ButtonAction.UNDO, 7)
    assert read_payload(agree_payload(20261102)) == ButtonPress(ButtonAction.AGREE, 20261102)
    assert read_payload("tolak") == ButtonPress(ButtonAction.DECLINE)
