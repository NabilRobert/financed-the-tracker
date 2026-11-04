"""`ekspor` and `hapus data saya`: a person's way to take their records, or remove them.

Deleting takes two messages, and the second only counts within ten minutes, because a
borrowed phone is enough to send one (T11). After it, the person simply stops existing
here: a repeat of the message finds a stranger. The app only records the confirmation;
the owner job deletes them within 15 minutes, because the app login can't delete (M1).
"""

from typing import Final

from tracker.channel import replies
from tracker.channel.gateway import Attachment
from tracker.ledger.export import write_person_csv
from tracker.ledger.people import confirm_deletion, has_open_deletion_request, request_deletion
from tracker.ledger.scope import PersonScope

EXPORT_WORD: Final = "ekspor"
DELETE_REQUEST: Final = "hapus data saya"
DELETE_CONFIRMATION: Final = "hapus semua"
DELETE_COMMAND_WORD: Final = "hapus"


async def _export(scope: PersonScope) -> Attachment:
    csv_text = await write_person_csv(scope.connection, scope.person_id)
    return Attachment(replies.EXPORT_FILENAME, csv_text.encode(), replies.EXPORT_CAPTION)


async def _confirm_deletion(scope: PersonScope) -> str:
    if not await has_open_deletion_request(scope):
        return replies.DELETE_HOW
    await confirm_deletion(scope)
    return replies.DELETED


async def answer_leaving(scope: PersonScope, word: str) -> str | Attachment | None:
    """The reply if this message is about leaving, otherwise None."""
    if word == EXPORT_WORD:
        return await _export(scope)
    if word == DELETE_REQUEST:
        await request_deletion(scope)
        return replies.DELETE_CONFIRM
    if word == DELETE_CONFIRMATION:
        return await _confirm_deletion(scope)
    return replies.DELETE_HOW if word.split(" ")[0] == DELETE_COMMAND_WORD else None
