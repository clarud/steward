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


def test_find_card_offers_one_root_or_all_roots_again(monkeypatch) -> None:
    tut = source(4, "tut04.pdf")
    result = FindResult("picks", ((FileCandidate(tut, None, 1.0, frozenset({"keyword"})), "AVX2"),))
    finder = app(monkeypatch, find=result)

    everywhere = finder.find("100", "avx", FindScope((SourceType.PDF,)), roots=["Y4S1", "Notes"])
    only = finder.find("100", "avx", FindScope((SourceType.PDF,), "Y4S1"), roots=["Y4S1", "Notes"])
    single = finder.find("100", "avx", FindScope(), roots=["Y4S1"])

    assert [(a.label, a.command) for a in everywhere.actions[2:]] == [
        ("Only Y4S1", '/find avx --type pdf --root "Y4S1"'),
        ("Only Notes", '/find avx --type pdf --root "Notes"'),
    ]
    assert [(a.label, a.command) for a in only.actions[2:]] == [("Search all folders", "/find avx --type pdf")]
    assert len(single.actions) == 2


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
    assert card.text.startswith("Static splits evenly. [p.3]")  # the key reads as a page, not [F70]
    assert "Sources:\n• Y4S1 / CS3210 / openmp.pdf: p.3" in card.text
    assert [action.command for action in card.actions] == ["/source 7"]


def test_removed_statements_can_be_shown_from_the_answer_card(monkeypatch) -> None:
    notes = source(7, "openmp.pdf")
    fragment = SourceFragment(70, 7, None, 0, "Static splits evenly.", "page 3")
    result = AskResult(
        "answered", "Static splits evenly. [F70]", (Evidence("F70", notes, fragment),), removed=1,
        removed_text=("Static is always fastest. [F70]",),
    )
    answers = app(monkeypatch, ask=result)

    card = answers.ask("100", "static?")

    assert card.actions[-1].label == "Show removed (1)" and card.actions[-1].command == "/ask_removed"
    assert "Static is always fastest. [p.3]" in answers.removed("100").text
    assert answers.removed("200") == "Nothing was removed from your latest answer or summary."


def test_summary_card_reports_coverage_and_skipped_parts(monkeypatch) -> None:
    lecture = source(9, "lecture.pptx")
    result = SummaryResult("partial", "Covers loops [F1].", (("F1", "slide 1"),), 30, 40, ("slide 12 – slide 13",))

    card = app(monkeypatch, summarize=result).summarize(lecture)

    assert "Covered 30 of 40 sections · couldn't summarise slide 12 – slide 13." in card.text
    assert card.text.startswith("Covers loops [slide 1].") and "[F1]" not in card.text
    assert card.title == "Summary: lecture.pptx"


def test_an_answer_citing_two_files_numbers_them_and_renders_maths(monkeypatch) -> None:
    queueing, notes = source(3, "02-Queueing.pdf"), source(4, "notes.md")
    evidence = (
        Evidence("F30", queueing, SourceFragment(30, 3, None, 0, "E(W) = 1/(mu - lambda)", "page 19")),
        Evidence("F40", notes, SourceFragment(40, 4, None, 0, "Little's law", "lines 12-40")),
    )
    text = r"The sojourn time is $E(W) = 1/(\mu - \lambda)$ [F30]. Little's law relates $L$ and $W$ [F40][F30]."
    card = app(monkeypatch, ask=AskResult("answered", text, evidence)).ask("100", "sojourn?")

    assert card.text.startswith(
        "The sojourn time is E(W) = 1/(μ - λ) [1 p.19]. Little's law relates L and W [2 lines 12–40, 1 p.19]."
    )
    assert "Sources:\n1. Y4S1 / CS3210 / 02-Queueing.pdf: p.19\n2. Y4S1 / CS3210 / notes.md: lines 12–40" in card.text


def test_a_summary_shows_how_many_statements_were_removed_and_can_list_them(monkeypatch) -> None:
    lecture = source(9, "lecture.pptx")
    result = SummaryResult(
        "done", "Covers loops [F1].", (("F1", "slide 1"),), 1, 2,
        removed_text=("Loops were invented in 1850 [F2].",), removed_cited=(("F2", "slide 2"),),
    )
    answers = app(monkeypatch, summarize=result)

    card = answers.summarize(lecture, "100")

    assert "(1 statement removed: not supported by your files.)" in card.text
    assert card.actions[-1].label == "Show removed (1)"
    assert "Loops were invented in 1850 [slide 2]." in answers.removed("100").text
