import pytest

from steward.research import DuckDuckGoSearchProvider, GeminiGoogleSearchProvider, ResearchBundle, ResearchProviderError, ResearchService, ResearchSource


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
