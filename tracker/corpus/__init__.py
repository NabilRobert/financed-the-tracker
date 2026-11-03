"""The labelled corpus: hand-written messages with the answer a careful reader gives.

It lives in the package, not in tests/, so another project can depend on it at
a pinned commit instead of copying it (Project 14 trains on the dev set). The
format and the labelling rules are in README.md beside this file.

The held-out set is for measuring only. Nothing may be written, tuned or
trained against it, here or in any project that imports it, or its score
stops meaning anything (docs/parser-accuracy.md).
"""

from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

CORPUS_DIR = Path(__file__).parent
DEV_CORPUS = CORPUS_DIR / "dev.jsonl"
HOLDOUT_CORPUS = CORPUS_DIR / "holdout.jsonl"


class Outcome(StrEnum):
    ENTRY = "entry"
    ASK = "ask"
    UNPARSED = "unparsed"


class CorpusMessage(BaseModel):
    """One labelled line."""

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
