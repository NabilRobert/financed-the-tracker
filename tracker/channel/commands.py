"""The words the bot treats as commands: rekap, budget, kategori, bantuan and so on."""

from datetime import date
from typing import Final

from tracker.channel import replies
from tracker.ledger.budgets import list_budgets, set_budget
from tracker.ledger.categories import CategoryChange, change_entry_category, latest_entry_id
from tracker.ledger.scope import PersonScope
from tracker.ledger.summaries import Period, month_period, summarise, week_period
from tracker.parsing.amount import clean_text, parse_amount

MONTHS: Final[dict[str, int]] = {
    "januari": 1,
    "februari": 2,
    "maret": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "agustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "desember": 12,
}
WEEK_WORDS: Final = ("minggu ini",)


def recap_period(words: list[str], today: date) -> Period | None:
    """`rekap maret` means the latest March that isn't in the future."""
    if not words:
        return month_period(today.year, today.month)
    if " ".join(words) in WEEK_WORDS:
        return week_period(today)
    if len(words) == 1 and words[0] in MONTHS:
        month = MONTHS[words[0]]
        return month_period(today.year if month <= today.month else today.year - 1, month)
    return None


async def _recap(scope: PersonScope, words: list[str], today: date) -> str:
    period = recap_period(words, today)
    if period is None:
        return replies.RECAP_UNKNOWN
    return replies.recap(await summarise(scope, period))


async def _budget(scope: PersonScope, words: list[str], today: date) -> str:
    if not words:
        return replies.budget_list(await list_budgets(scope, today))
    if len(words) < 2:
        return replies.BUDGET_HOW
    category, amount_text = words[0], " ".join(words[1:])
    limit_rupiah = parse_amount(amount_text)
    if not isinstance(limit_rupiah, int):
        return replies.BUDGET_HOW
    if not await set_budget(scope, category, limit_rupiah):
        return replies.BUDGET_UNKNOWN_CATEGORY
    return replies.budget_set(category.capitalize(), limit_rupiah)


async def _category(scope: PersonScope, words: list[str]) -> str:
    if not words:
        return replies.CATEGORY_HOW
    entry_id = await latest_entry_id(scope)
    if entry_id is None:
        return replies.NO_ENTRY_YET
    category = " ".join(words)
    if await change_entry_category(scope, entry_id, category) is not CategoryChange.CHANGED:
        return replies.CATEGORY_UNKNOWN
    return replies.category_changed(category.capitalize())


async def run_command(scope: PersonScope, text: str, today: date, human_contact: str) -> str | None:
    """The reply to a command, or None if the message isn't one."""
    command, *words = clean_text(text).split() or [""]
    if command == "rekap":
        return await _recap(scope, words, today)
    if command == "budget":
        return await _budget(scope, words, today)
    if command == "kategori":
        return await _category(scope, words)
    if command == "bantuan":
        return replies.help_text(human_contact)
    return None
