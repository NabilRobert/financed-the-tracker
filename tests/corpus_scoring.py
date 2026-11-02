"""Loads the labelled corpus and scores the rules against it.

test_corpus.py gates on these numbers, and conftest.py prints them after every
run. What each number means is defined in docs/parser-accuracy.md.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from tracker.parsing.message import AmbiguousEntry, ParsedEntry, parse_message

CORPUS_DIR = Path(__file__).parent / "corpus"
DEV_CORPUS = CORPUS_DIR / "dev.jsonl"
HOLDOUT_CORPUS = CORPUS_DIR / "holdout.jsonl"


class Outcome(StrEnum):
    ENTRY = "entry"
    ASK = "ask"
    UNPARSED = "unparsed"


class CorpusMessage(BaseModel):
    """One labelled line. The format is in tests/corpus/README.md."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    outcome: Outcome
    amount: int | None = None
    direction: Literal["spending", "income"] | None = None
    category: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def entry_fields_appear_only_on_entries(self) -> Self:
        fields = (self.amount, self.direction, self.category)
        has_all_fields = all(field is not None for field in fields)
        has_no_fields = all(field is None for field in fields)
        if self.outcome is Outcome.ENTRY and not has_all_fields:
            raise ValueError(f"an entry needs amount, direction and category: {self.text!r}")
        if self.outcome is not Outcome.ENTRY and not has_no_fields:
            raise ValueError(f"only an entry has amount, direction and category: {self.text!r}")
        return self


def load_corpus(path: Path) -> list[CorpusMessage]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [CorpusMessage.model_validate_json(line) for line in lines if line]


def rules_outcome(text: str) -> Outcome:
    """What the rules do with a message, in the corpus's own words."""
    match parse_message(text):
        case ParsedEntry():
            return Outcome.ENTRY
        case AmbiguousEntry():
            return Outcome.ASK
        case None:
            return Outcome.UNPARSED


@dataclass
class CorpusScore:
    """Counts over one corpus. The lists hold the message texts, so a failure names them."""

    messages: int = 0
    labelled_entries: int = 0
    labelled_money: int = 0
    written_correctly: int = 0
    wrong_amounts: list[str] = field(default_factory=list[str])
    false_writes: list[str] = field(default_factory=list[str])
    needless_questions: list[str] = field(default_factory=list[str])
    wrong_directions: list[str] = field(default_factory=list[str])
    wrong_categories: list[str] = field(default_factory=list[str])
    missed: list[str] = field(default_factory=list[str])

    @property
    def coverage(self) -> float:
        """G2: of the messages about money, the share that never needs a model."""
        return (self.labelled_money - len(self.missed)) / self.labelled_money

    @property
    def straight_write_rate(self) -> float:
        """Of the messages a reader is sure about, the share written with no question."""
        return self.written_correctly / self.labelled_entries


def _score_written_entry(score: CorpusScore, message: CorpusMessage, entry: ParsedEntry) -> None:
    if entry.amount_rupiah != message.amount:
        score.wrong_amounts.append(message.text)
        return
    score.written_correctly += 1
    if entry.direction != message.direction:
        score.wrong_directions.append(message.text)
    if entry.category != message.category:
        score.wrong_categories.append(message.text)


def score_corpus(messages: list[CorpusMessage]) -> CorpusScore:
    score = CorpusScore(messages=len(messages))
    for message in messages:
        result = parse_message(message.text)
        is_labelled_entry = message.outcome is Outcome.ENTRY
        is_labelled_money = message.outcome is not Outcome.UNPARSED
        score.labelled_entries += is_labelled_entry
        score.labelled_money += is_labelled_money
        if isinstance(result, ParsedEntry):
            if is_labelled_entry:
                _score_written_entry(score, message, result)
            else:
                score.false_writes.append(message.text)
        elif isinstance(result, AmbiguousEntry) and is_labelled_entry:
            score.needless_questions.append(message.text)
        elif result is None and is_labelled_money:
            score.missed.append(message.text)
    return score


def format_score(name: str, score: CorpusScore) -> str:
    return (
        f"{name}: {score.messages} messages | wrong amounts {len(score.wrong_amounts)} | "
        f"false writes {len(score.false_writes)} | coverage {score.coverage:.1%} | "
        f"straight writes {score.straight_write_rate:.1%} ({score.written_correctly}/{score.labelled_entries}) | "
        f"needless questions {len(score.needless_questions)} | missed {len(score.missed)} | "
        f"wrong direction {len(score.wrong_directions)} | wrong category {len(score.wrong_categories)}"
    )
