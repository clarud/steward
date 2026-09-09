from pathlib import Path

from steward.extraction import MarkdownExtractor, SourceFragmentRepository
from steward.retrieval import LexicalSearchService
from steward.sources import SourceRepository
from steward.sources.service import SourceService
from steward.storage import initialize_database
from steward.evaluation import (
    evaluate_lexical_retrieval,
    load_retrieval_cases,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "retrieval_vault"
CASES_PATH = Path(__file__).with_name("retrieval_cases.yaml")


def test_lexical_retrieval_meets_initial_evaluation_baseline(tmp_path: Path) -> None:
    database_path = tmp_path / "steward.db"
    initialize_database(database_path)
    source_repository = SourceRepository(database_path)
    fragment_repository = SourceFragmentRepository(database_path)
    SourceService(
        source_repository=source_repository,
        fragment_repository=fragment_repository,
        markdown_extractor=MarkdownExtractor(),
    ).scan_markdown_root(FIXTURE_ROOT)
    lexical_service = LexicalSearchService(source_repository, fragment_repository)

    evaluation = evaluate_lexical_retrieval(
        lexical_service,
        load_retrieval_cases(CASES_PATH),
        FIXTURE_ROOT,
    )

    assert evaluation.case_count == 20
    assert evaluation.recall_at_5 >= 0.95
    assert evaluation.mean_reciprocal_rank >= 0.9
