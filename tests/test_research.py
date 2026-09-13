from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

import pytest

from steward.research import DuckDuckGoSearchProvider, EphemeralResearchCardRepository, GeminiGoogleSearchProvider, ResearchBundle, ResearchProviderError, ResearchService, ResearchSource
from steward.storage import initialize_database


class FakeProvider:
    def __init__(self) -> None:
        self.queries = []

    def research(self, query: str) -> ResearchBundle:
        self.queries.append(query)
        return ResearchBundle(query, "answer", (ResearchSource("Example", "https://example.com"),))


def test_research_service_keeps_external_results_ephemeral() -> None:
    provider = FakeProvider()
    bundle = ResearchService(provider).research("  TLB shootdowns  ")

    assert provider.queries == ["TLB shootdowns"]
    assert bundle.retention == "ephemeral"
    assert bundle.sources[0].url == "https://example.com"


def test_research_service_rejects_empty_question() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        ResearchService(FakeProvider()).research(" ")


def test_corrupt_ephemeral_research_card_is_dropped_without_becoming_a_source(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"; initialize_database(database)
    cards = EphemeralResearchCardRepository(database)
    cards.add(
        "temporary", "100", ResearchBundle("TLB", "answer", (ResearchSource("Example", "https://example.com"),)),
        datetime.now(UTC) + timedelta(minutes=1),
    )
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE ephemeral_research_cards SET sources_json = 'not json' WHERE token = 'temporary'")

    assert cards.get("temporary", "100") is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM ephemeral_research_cards").fetchone()[0] == 0


def test_gemini_provider_collects_grounded_web_sources() -> None:
    class Web:
        uri = "https://example.com/tlb"
        title = "TLB article"

    class Chunk:
        web = Web()

    class Metadata:
        grounding_chunks = [Chunk(), Chunk()]

    class Candidate:
        grounding_metadata = Metadata()

    class Response:
        text = "TLB shootdowns invalidate remote translations."
        candidates = [Candidate()]

    class Models:
        def generate_content(self, **kwargs):
            self.kwargs = kwargs
            return Response()

    class Client:
        models = Models()

    bundle = GeminiGoogleSearchProvider(api_key="key", model="gemini-test", client=Client()).research("TLB shootdowns")

    assert bundle.answer.startswith("TLB")
    assert bundle.sources == (ResearchSource("TLB article", "https://example.com/tlb"),)


def test_gemini_provider_translates_external_failure() -> None:
    class Models:
        def generate_content(self, **_kwargs):
            raise OSError("offline")

    class Client:
        models = Models()

    with pytest.raises(ResearchProviderError, match="temporarily unavailable"):
        GeminiGoogleSearchProvider(api_key="key", model="gemini-test", client=Client()).research("TLB")


def test_duckduckgo_provider_returns_labeled_result_snippets() -> None:
    html = """
    <a class='result__a' href='https://example.com/tlb'>TLB guide</a>
    <div class='result__snippet'>A cache for recent translations.</div>
    """
    bundle = DuckDuckGoSearchProvider(fetch=lambda _query: html).research("TLB")

    assert bundle.provider == "duckduckgo_search"
    assert bundle.sources == (
        ResearchSource("TLB guide", "https://example.com/tlb", "A cache for recent translations."),
    )
    assert "not a synthesized reading" in bundle.answer


def test_duckduckgo_provider_translates_network_failure() -> None:
    def fail(_query: str) -> str:
        raise OSError("offline")

    with pytest.raises(ResearchProviderError, match="temporarily unavailable"):
        DuckDuckGoSearchProvider(fetch=fail).research("TLB")
