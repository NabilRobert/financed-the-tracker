"""The corpus as a specification and a gate.

Every dev message pins one behaviour. The held-out set is only ever scored as a
whole, so nobody reads a failure there one message at a time and tunes to it.
"""

import pytest
from corpus_scoring import (
    DEV_CORPUS,
    HOLDOUT_CORPUS,
    CorpusMessage,
    CorpusScore,
    Outcome,
    load_corpus,
    rules_outcome,
    score_corpus,
)

from tracker.parsing.message import ParsedEntry, parse_message

MIN_COVERAGE = 0.85  # G2 in docs/planning/phase-1-problem-brief.md, confirmed in docs/parser-accuracy.md.


def _dev_message_id(message: CorpusMessage) -> str:
    return message.text or "(empty)"


@pytest.mark.parametrize("message", load_corpus(DEV_CORPUS), ids=_dev_message_id)
def test_dev_message_gets_its_labelled_outcome(message: CorpusMessage) -> None:
    assert rules_outcome(message.text) is message.outcome
    if message.outcome is Outcome.ENTRY:
        entry = parse_message(message.text)
        assert isinstance(entry, ParsedEntry)
        assert (entry.amount_rupiah, entry.direction, entry.category) == (
            message.amount,
            message.direction,
            message.category,
        )


@pytest.fixture(scope="module")
def holdout_score() -> CorpusScore:
    return score_corpus(load_corpus(HOLDOUT_CORPUS))


def test_holdout_has_no_wrong_amounts(holdout_score: CorpusScore) -> None:
    assert holdout_score.wrong_amounts == []


def test_holdout_has_no_false_writes(holdout_score: CorpusScore) -> None:
    assert holdout_score.false_writes == []


def test_holdout_coverage_meets_the_goal(holdout_score: CorpusScore) -> None:
    assert holdout_score.coverage >= MIN_COVERAGE
