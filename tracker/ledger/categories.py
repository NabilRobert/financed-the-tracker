"""Which category an entry goes in, and the words a person teaches it (C2, C3)."""

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import text

from tracker.ledger.scope import PersonScope
from tracker.parsing.message import WORD_PATTERN, Direction


@dataclass(frozen=True)
class Category:
    id: int
    name: str


class CategoryChange(StrEnum):
    CHANGED = "changed"
    ENTRY_NOT_FOUND = "entry_not_found"
    UNKNOWN_CATEGORY = "unknown_category"


async def resolve_category(scope: PersonScope, description: str, direction: Direction, parsed_name: str) -> Category:
    """A word the person taught beats the parser's starter keywords, or C3 would change nothing."""
    words = WORD_PATTERN.findall(description)
    rows = await scope.connection.execute(
        text("""
            SELECT k.word, c.id, c.name
            FROM category_keyword k JOIN category c ON c.id = k.category_id
            WHERE k.person_id = :person_id AND c.direction = :direction AND k.word = ANY(CAST(:words AS text[]))
        """),
        {"person_id": scope.person_id, "direction": direction, "words": words},
    )
    taught = {row.word: Category(row.id, row.name) for row in rows}
    for word in words:
        if word in taught:
            return taught[word]
    row = (
        await scope.connection.execute(
            text("SELECT id, name FROM category WHERE person_id = :person_id AND name = :name"),
            {"person_id": scope.person_id, "name": parsed_name},
        )
    ).one()
    return Category(row.id, row.name)


async def change_entry_category(scope: PersonScope, entry_id: int, category_name: str) -> CategoryChange:
    """Moves a live entry to a category with its direction, and remembers its first word (C3)."""
    entry = (
        await scope.connection.execute(
            text("""
                SELECT direction, description FROM entry
                WHERE id = :entry_id AND person_id = :person_id AND voided_at IS NULL
            """),
            {"entry_id": entry_id, "person_id": scope.person_id},
        )
    ).one_or_none()
    if entry is None:
        return CategoryChange.ENTRY_NOT_FOUND
    category_id = await scope.connection.scalar(
        text("""
            SELECT id FROM category
            WHERE person_id = :person_id AND lower(name) = lower(:name) AND direction = :direction
        """),
        {"person_id": scope.person_id, "name": category_name, "direction": entry.direction},
    )
    if category_id is None:
        return CategoryChange.UNKNOWN_CATEGORY
    await scope.connection.execute(
        text("UPDATE entry SET category_id = :category_id WHERE id = :entry_id AND person_id = :person_id"),
        {"category_id": category_id, "entry_id": entry_id, "person_id": scope.person_id},
    )
    words = WORD_PATTERN.findall(entry.description)
    if words:
        await _remember_word(scope, words[0], category_id)
    return CategoryChange.CHANGED


async def _remember_word(scope: PersonScope, word: str, category_id: int) -> None:
    await scope.connection.execute(
        text("""
            INSERT INTO category_keyword (person_id, word, category_id) VALUES (:person_id, :word, :category_id)
            ON CONFLICT (person_id, word) DO UPDATE SET category_id = EXCLUDED.category_id
        """),
        {"person_id": scope.person_id, "word": word, "category_id": category_id},
    )
