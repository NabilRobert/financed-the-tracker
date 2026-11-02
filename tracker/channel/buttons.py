"""What a reply button carries, and reading it back.

A payload arrives inside a signed webhook, but the person's own device sends it, so
it's still input: it must parse exactly, and every id in it is checked against the
sender by the ledger (L4, T10).
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

MAX_BIGINT: Final = 2**63 - 1  # A bigger id would crash the query instead of finding nothing.
_PAYLOAD_PATTERN: Final = re.compile(r"(?P<action>[a-z]+)(?::(?P<target>[1-9][0-9]{0,18}))?(?::(?P<choice>[01]))?")


class ButtonAction(StrEnum):
    AGREE = "setuju"
    DECLINE = "tolak"
    CONFIRM = "ya"
    CANCEL = "batal"
    UNDO = "undo"


@dataclass(frozen=True)
class ButtonPress:
    action: ButtonAction
    target_id: int | None = None
    choice: int = 0


def confirm_payload(pending_id: int, choice: int) -> str:
    return f"{ButtonAction.CONFIRM}:{pending_id}:{choice}"


def cancel_payload(pending_id: int) -> str:
    return f"{ButtonAction.CANCEL}:{pending_id}"


def undo_payload(entry_id: int) -> str:
    return f"{ButtonAction.UNDO}:{entry_id}"


def agree_payload(notice_version: int) -> str:
    """Carries the version, so an old button can't agree to a notice its owner never saw."""
    return f"{ButtonAction.AGREE}:{notice_version}"


def _target_fits_action(action: ButtonAction, target_id: int | None, choice: str | None) -> bool:
    if action is ButtonAction.DECLINE:
        return target_id is None and choice is None
    if action is ButtonAction.CONFIRM:
        return target_id is not None and choice is not None
    return target_id is not None and choice is None


def read_payload(payload: str) -> ButtonPress | None:
    """The button pressed, or None for anything this bot didn't send."""
    match = _PAYLOAD_PATTERN.fullmatch(payload)
    if match is None or match["action"] not in tuple(ButtonAction):
        return None
    action = ButtonAction(match["action"])
    target_id = int(match["target"]) if match["target"] else None
    if target_id is not None and target_id > MAX_BIGINT:
        return None
    if not _target_fits_action(action, target_id, match["choice"]):
        return None
    return ButtonPress(action, target_id, int(match["choice"] or 0))
