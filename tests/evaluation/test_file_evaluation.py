from pathlib import Path

import pytest

from steward.evaluation import (
    cites_expected, declines, evaluate_checker, evaluate_files, load_ask_cases, load_checker_cases, load_file_cases,
)
from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.retrieval import LexicalSearchService
from steward.retrieval.files import group_by_file
from steward.sources import SourceRepository
from steward.sources.service import SourceService
from steward.storage import initialize_database

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "retrieval_vault"
CASES_PATH = Path(__file__).with_name("retrieval_cases.yaml")


def test_keyword_search_finds_the_expected_file_for_the_fixture_cases(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    sources = SourceRepository(database)
    fragments = SourceFragmentRepository(database)
    SourceService(sources, fragments, MarkdownExtractor()).scan_source_root(FIXTURE_ROOT)
    lexical = LexicalSearchService(sources, fragments)

    evaluation = evaluate_files(
        lambda query: [candidate.source.path for candidate in group_by_file(lexical.search(query, limit=20), "keyword")],
        load_file_cases(CASES_PATH),
    )

    assert evaluation.case_count == 20
    assert evaluation.hit_at_3 >= 0.95
    assert evaluation.mean_reciprocal_rank >= 0.9


def test_scores_count_rank_and_list_misses(tmp_path: Path) -> None:
    cases = tmp_path / "cases.yaml"
    cases.write_text(
        "cases:\n  - {query: a, file: CS3210/tut04.pdf}\n  - {query: b, file: notes.md}\n  - {query: c, file: gone.md}\n",
        encoding="utf-8",
    )
    ranking = {
        "a": [Path("C:/Y4S1/CS3210/tut04.pdf")],
        "b": [Path("x.md"), Path("C:/Y4S1/notes.md")],
        "c": [Path("x.md")],
    }

    result = evaluate_files(lambda query: ranking[query], load_file_cases(cases))

    assert (result.hit_at_1, result.hit_at_3) == (1 / 3, 2 / 3)
    assert round(result.mean_reciprocal_rank, 3) == 0.5
    assert result.misses == (("c", "gone.md"),)


def test_a_case_can_accept_any_of_several_copies(tmp_path: Path) -> None:
    cases = tmp_path / "cases.yaml"
    cases.write_text(
        "cases:\n  - {query: q, files: [CS3210/Lectures/L03.pdf, Transcripts/Cleaned/L03.md]}\n",
        encoding="utf-8",
    )

    result = evaluate_files(
        lambda query: [Path("x.md"), Path("C:/Y4S1/CS3210/Transcripts/Cleaned/L03.md")], load_file_cases(cases),
    )

    assert (result.hit_at_1, result.hit_at_3) == (0.0, 1.0)


def test_checker_evaluation_counts_planted_and_true_statements(tmp_path: Path) -> None:
    cases = tmp_path / "checker.yaml"
    cases.write_text(
        "cases:\n"
        "  - evidence: {F1: 'Amdahl bounds speedup by the serial fraction', F2: 'GPUs run warps of 32 threads'}\n"
        "    supported: ['Speedup is limited by the serial part', {text: 'A warp has 32 threads', cite: F2}]\n"
        "    unsupported: ['Amdahl says speedup grows without limit', {text: 'A warp has 32 threads', cite: F1}]\n",
        encoding="utf-8",
    )
    loaded = load_checker_cases(cases)
    assert [item.cite for item in loaded[0].statements] == ["F1", "F2", "F1", "F1"]

    def check(answer: str, evidence: dict[str, str]) -> str:
        # Removes the planted "without limit" claim but misses the miscited one.
        return "\n".join(line for line in answer.splitlines() if "without limit" not in line)

    result = evaluate_checker(check, loaded)

    assert (result.supported, result.supported_kept) == (2, 2)
    assert (result.unsupported, result.unsupported_removed) == (2, 1)
    assert result.missed == ("A warp has 32 threads",)


def test_checker_cases_reject_statements_the_checker_would_split(tmp_path: Path) -> None:
    cases = tmp_path / "checker.yaml"
    cases.write_text("cases:\n  - evidence: x\n    supported: ['Clock is 3.5 GHz']\n", encoding="utf-8")

    with pytest.raises(ValueError, match="one sentence"):
        load_checker_cases(cases)


def test_ask_cases_and_cited_file_matching(tmp_path: Path) -> None:
    cases = tmp_path / "ask.yaml"
    cases.write_text(
        "cases:\n  - {question: q1, files: [L05-Performance.pdf]}\n  - {question: q2, answerable: false}\n",
        encoding="utf-8",
    )

    loaded = load_ask_cases(cases)

    assert loaded[1].expected == ()
    assert cites_expected([Path("C:/Y4S1/CS3210/Lectures/L05-Performance.pdf")], loaded[0].expected)
    assert not cites_expected([Path("C:/x/L06-GPGPU.pdf")], loaded[0].expected)



def test_an_uncited_answer_only_counts_as_declining_if_it_says_so() -> None:
    assert declines("no_evidence", "I couldn't find anything.", [])
    assert declines("answered", "Your files don't mention MESI.", [])
    assert not declines("answered", "MESI has four states: modified, exclusive, shared, invalid.", [])
    assert not declines("answered", "It isn't covered [F1].", [Path("x.md")])
