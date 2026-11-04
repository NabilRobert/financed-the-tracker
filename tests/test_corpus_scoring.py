"""The scorer's own tests: a gate that can't fail proves nothing."""

from pathlib import Path

import pytest
from corpus_scoring import score_corpus
from pydantic import ValidationError

from tracker.corpus import CorpusMessage, Outcome, load_corpus


def entry(text: str, amount: int, direction: str = "spending", category: str = "Makan") -> CorpusMessage:
    return CorpusMessage.model_validate(
        {"text": text, "outcome": "entry", "amount": amount, "direction": direction, "category": category}
    )


def test_a_written_amount_that_differs_from_the_label_is_a_wrong_amount() -> None:
    score = score_corpus([entry("makan 25rb", 26000)])
    assert score.wrong_amounts == ["makan 25rb"]
    assert score.written_correctly == 0


def test_a_write_on_a_message_labelled_ask_is_a_false_write() -> None:
    score = score_corpus([CorpusMessage(text="makan 25rb", outcome=Outcome.ASK)])
    assert score.false_writes == ["makan 25rb"]


def test_a_write_on_a_message_labelled_unparsed_is_a_false_write() -> None:
    score = score_corpus([CorpusMessage(text="makan 25rb", outcome=Outcome.UNPARSED)])
    assert score.false_writes == ["makan 25rb"]


def test_a_question_on_a_sure_message_is_needless_but_not_wrong() -> None:
    score = score_corpus([entry("kopi 25", 25000)])
    assert score.needless_questions == ["kopi 25"]
    assert score.wrong_amounts == []


def test_money_the_rules_cannot_read_counts_against_coverage() -> None:
    score = score_corpus([entry("makan 25rb", 25000), entry("lima ribu", 5000)])
    assert score.missed == ["lima ribu"]
    assert score.coverage == 0.5


def test_a_question_counts_as_covered_because_no_model_is_needed() -> None:
    assert score_corpus([CorpusMessage(text="kopi 25", outcome=Outcome.ASK)]).coverage == 1


def test_right_amount_with_wrong_direction_and_category_is_counted_separately() -> None:
    score = score_corpus([entry("makan 25rb", 25000, direction="income", category="Gaji")])
    assert score.written_correctly == 1
    assert score.wrong_directions == ["makan 25rb"]
    assert score.wrong_categories == ["makan 25rb"]


def test_an_entry_label_without_an_amount_is_refused() -> None:
    with pytest.raises(ValidationError, match="an entry needs"):
        CorpusMessage(text="makan", outcome=Outcome.ENTRY)


def test_an_ask_label_with_an_amount_is_refused() -> None:
    with pytest.raises(ValidationError, match="only an entry has"):
        CorpusMessage(text="kopi 25", outcome=Outcome.ASK, amount=25)


def test_an_unknown_label_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        CorpusMessage.model_validate({"text": "x", "outcome": "ask", "amonut": 5})


def test_load_corpus_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "corpus.jsonl"
    path.write_text('{"text": "halo", "outcome": "unparsed"}\n\n', encoding="utf-8")
    assert len(load_corpus(path)) == 1
