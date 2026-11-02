"""pywa's handlers: they turn typed updates into calls on Conversation, then send the reply.

The reply goes out after the transaction has committed, so a failed send can't lose an
entry, and answering 500 for it would only make Meta repeat a message that's already
handled. A failed handler is the opposite: it answers 500, so Meta retries (R4).
"""

from collections.abc import Awaitable
from typing import Any, Final

from pywa.handlers import CallbackButtonHandler, MessageHandler, PhoneNumberChangeHandler
from pywa.types.chat import ChatType
from pywa.types.others import MessageType
from pywa_async.client import WhatsApp
from pywa_async.types.callback import Button, CallbackButton
from pywa_async.types.message import Message
from pywa_async.types.system import PhoneNumberChange

from tracker.channel.conversation import Conversation, Sender
from tracker.channel.failures import logger, note_failure
from tracker.channel.replies import Reply
from tracker.ledger.people import change_bsuid

SILENT_TYPES: Final = (MessageType.REACTION, MessageType.UNSUPPORTED)


async def _send(client: WhatsApp, to: str, reply: Reply) -> None:
    buttons = [Button(title=button.title, callback_data=button.payload) for button in reply.buttons]
    try:
        await client.send_message(to=to, text=reply.text, buttons=buttons or None)
    except Exception as error:  # any send failure is reported the same way, by type alone.
        logger.error("reply_failed error_type=%s", type(error).__name__)


async def _answer(client: WhatsApp, to: str, work: Awaitable[Reply | None]) -> None:
    try:
        reply = await work
    except Exception as error:  # see failures.py: pywa would log it and answer 200.
        note_failure(type(error).__name__, "handler")
        return
    if reply is not None:
        await _send(client, to, reply)


def _previous_bsuid(change: PhoneNumberChange) -> str | None:
    """pywa doesn't surface previous_user_id (ADR-008), so it's read from the signed update."""
    raw_update: dict[str, Any] = dict(change.raw)
    system: dict[str, Any] = raw_update["entry"][0]["changes"][0]["value"]["messages"][0]["system"]
    return system.get("previous_user_id")


def register_handlers(wa: WhatsApp, conversation: Conversation) -> None:
    async def on_message(client: WhatsApp, message: Message) -> None:
        if message.chat.type is ChatType.GROUP or message.type in SILENT_TYPES:
            return  # A ledger reply must never land in a group.
        sender = Sender(message.from_user.bsuid, message.from_user.wa_id)
        if message.type is MessageType.TEXT and message.text is not None:
            work = conversation.handle_text(sender, message.id, message.text, message.timestamp)
        else:
            work = conversation.handle_other_message(sender, message.id)
        await _answer(client, message.from_user.bsuid, work)

    async def on_button(client: WhatsApp, press: CallbackButton[str]) -> None:
        sender = Sender(press.from_user.bsuid, press.from_user.wa_id)
        await _answer(client, press.from_user.bsuid, conversation.handle_button(sender, press.id, press.data))

    async def on_number_change(_: WhatsApp, change: PhoneNumberChange) -> None:
        old_bsuid, new_bsuid = _previous_bsuid(change), change.new_user_id
        if old_bsuid is None or new_bsuid is None:
            logger.warning("number_change_without_ids")
            return
        try:
            await change_bsuid(conversation.engine, old_bsuid, new_bsuid)
        except Exception as error:  # see failures.py.
            note_failure(type(error).__name__, "number_change")

    # pywa's handlers are typed for its sync classes, though the async client calls them just the same.
    wa.add_handlers(
        MessageHandler(on_message),  # pyright: ignore[reportArgumentType]
        CallbackButtonHandler(on_button),  # pyright: ignore[reportArgumentType]
        PhoneNumberChangeHandler(on_number_change),  # pyright: ignore[reportArgumentType]
    )
