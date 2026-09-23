"""How Find, Ask, and Summarize results look in Telegram."""

from datetime import UTC, datetime
from pathlib import Path

import steward.app.answers as answers_module
from steward.app.answers import StewardAnswersApplication
from steward.extraction import SourceFragment
from steward.graphs.ask import AskResult, Evidence
from steward.graphs.find import FindResult, FindScope
from steward.graphs.summarize import SummaryResult
from steward.retrieval.files import FileCandidate
from steward.sources import Source, SourceType

NOW = datetime(2026, 9, 24, tzinfo=UTC)


def source(identifier: int, name: str) -> Source:
    return Source(identifier, Path(f"C:/Y4S1/CS3210/{name}"), "a" * 64, SourceType.PDF, 1, NOW, NOW, NOW)


def app(monkeypatch, **results) -> StewardAnswersApplication:
    for flow, result in results.items():
        monkeypatch.setattr(answers_module, f"run_{flow}", lambda *_args, _result=result, **_kwargs: _result)
    return StewardAnswersApplication(
        find_graph=None, ask_graph=None, summarize_graph=None,
        location=lambda item: f"Y4S1 / CS3210 / {item.path.name}",
    )


def test_find_card_lists_picks_with_reasons_open_and_send(monkeypatch) -> None:
    tut = source(4, "tut04.pdf")
    result = FindResult("picks", ((FileCandidate(tut, None, 1.0, frozenset({"keyword"})), "AVX2 registers, page 9"),))

    card = app(monkeypatch, find=result).find("100", "avx tut 4", FindScope())

    assert card.text == "1. 📄 tut04.pdf\nY4S1 / CS3210 / tut04.pdf\nAVX2 registers, page 9"
    assert [action.command for action in card.actions] == ["/source 4", "/send_source 4"]
    assert "C:/" not in card.text


def test_find_card_asks_which_one_or_offers_ask_instead(monkeypatch) -> None:
    first, second = source(1, "a.pdf"), source(2, "b.pdf")
    clarify = FindResult("clarify", question="CS3210 or CS4226?", options=(
        FileCandidate(first, None, 1, frozenset()), FileCandidate(second, None, 1, frozenset()),
    ))
    card = app(monkeypatch, find=clarify).find("100", "tut 4", FindScope())
    assert card.title == "Which one?" and [a.command for a in card.actions] == ["/source 1", "/source 2"]

    missing = app(monkeypatch, find=FindResult("no_match", tried="words: zebra")).find("100", "zebra", FindScope())
    assert "I searched for words: zebra" in missing.text and missing.actions[0].command == "/ask zebra"


def test_ask_card_shows_answer_removed_count_and_sources(monkeypatch) -> None:
    notes = source(7, "openmp.pdf")
    fragment = SourceFragment(70, 7, None, 0, "Static splits evenly.", "page 3")
    result = AskResult("answered", "Static splits evenly. [F70]", (Evidence("F70", notes, fragment),), removed=1)

    card = app(monkeypatch, ask=result).ask("100", "static?")

    assert "(1 statement removed: not supported by your files.)" in card.text
    assert "Sources:\n• Y4S1 / CS3210 / openmp.pdf: page 3" in card.text
    assert [action.command for action in card.actions] == ["/source 7"]


def test_summary_card_reports_coverage_and_skipped_parts(monkeypatch) -> None:
    lecture = source(9, "lecture.pptx")
    result = SummaryResult("partial", "Covers loops [F1].", (("F1", "slide 1"),), 30, 40, ("slide 12 – slide 13",))

    card = app(monkeypatch, summarize=result).summarize(lecture)

    assert "Covered 30 of 40 sections · couldn't summarise slide 12 – slide 13." in card.text
    assert card.text.endswith("Sources: [F1] slide 1")
    assert card.title == "Summary: lecture.pptx"
