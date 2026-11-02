"""Prints the corpus scores at the end of every pytest run, so every CI log carries them."""

import pytest
from corpus_scoring import DEV_CORPUS, HOLDOUT_CORPUS, format_score, load_corpus, score_corpus


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    terminalreporter.section("parser accuracy (see docs/parser-accuracy.md)")
    terminalreporter.line(format_score("dev", score_corpus(load_corpus(DEV_CORPUS))))
    terminalreporter.line(format_score("held-out", score_corpus(load_corpus(HOLDOUT_CORPUS))))
