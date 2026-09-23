from pathlib import Path

from steward.evaluation import evaluate_files, load_file_cases
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
