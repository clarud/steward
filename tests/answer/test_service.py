from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from steward.answer import (
    AnswerService,
    ContextBuilder,
    GeminiModelGateway,
    ModelRouter,
    OpenAIModelGateway,
)
from steward.answer.service import GROUNDING_INSTRUCTIONS, MODEL_UNAVAILABLE_ANSWER, NO_EVIDENCE_ANSWER
from steward.answer.gateway import ModelGatewayError
from steward.extraction import SourceFragment
from steward.privacy import PrivacyRule, PrivacyService
from steward.retrieval import HybridSearchHit
from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database


@dataclass
class FakeRetriever:
    hits: tuple[HybridSearchHit, ...]
    received_query: str | None = None
    received_limit: int | None = None

    def search(self, query: str, *, limit: int = 5) -> tuple[HybridSearchHit, ...]:
        self.received_query = query
        self.received_limit = limit
        return self.hits


@dataclass
class FakeModelGateway:
    response: str = "A TLB caches translations. [F1]"
    instructions: str | None = None
    input_text: str | None = None

    def generate(self, *, instructions: str, input_text: str) -> str:
        self.instructions = instructions
        self.input_text = input_text
        return self.response


def _hit() -> HybridSearchHit:
    timestamp = datetime(2026, 9, 7, tzinfo=UTC)
    source = Source(
        id=1,
        path=Path("C:/vault/virtual-memory.md"),
        content_hash="a" * 64,
        source_type=SourceType.MARKDOWN,
        size_bytes=100,
        modified_at=timestamp,
        first_seen_at=timestamp,
        last_seen_at=timestamp,
    )
    fragment = SourceFragment(
        id=4,
        source_id=1,
        heading="TLB",
        ordinal=0,
        text="A TLB caches recently used address translations.",
        location="lines 9-12",
    )
    return HybridSearchHit(source, fragment, 0.03, -1.2, 0.8)


def test_answer_service_sends_only_retrieved_evidence_to_model() -> None:
    retriever = FakeRetriever((_hit(),))
    gateway = FakeModelGateway()
    service = AnswerService(retriever, ContextBuilder(), gateway)

    result = service.ask("What does a TLB do?")

    assert result.text == "A TLB caches translations. [F1]"
    assert result.citations[0].key == "F1"
    assert result.citations[0].fragment_id == 4
    assert result.citations[0].source_path == Path("C:/vault/virtual-memory.md")
    assert result.context is not None
    assert "What does a TLB do?" in result.context.prompt
    assert "A TLB caches recently used address translations." in result.context.prompt
    assert gateway.input_text == result.context.prompt
    assert gateway.instructions == GROUNDING_INSTRUCTIONS
    assert retriever.received_query == "What does a TLB do?"


def test_answer_service_does_not_call_model_without_local_evidence() -> None:
    gateway = FakeModelGateway()
    service = AnswerService(FakeRetriever(()), ContextBuilder(), gateway)

    result = service.ask("What did I decide about Redis?")

    assert result.text == NO_EVIDENCE_ANSWER
    assert result.citations == ()
    assert result.context is None
    assert gateway.input_text is None


def test_answer_service_returns_retrieval_context_when_the_model_is_unavailable() -> None:
    class FailingGateway:
        def generate(self, *, instructions: str, input_text: str) -> str:
            raise ModelGatewayError("rate limited")

    result = AnswerService(FakeRetriever((_hit(),)), ContextBuilder(), FailingGateway()).ask(
        "What does a TLB do?"
    )

    assert result.text == MODEL_UNAVAILABLE_ANSWER
    assert result.context is not None
    assert result.citations[0].fragment_id == 4


def test_answer_service_does_not_send_private_source_content_to_cloud_model(
    tmp_path: Path,
) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    hit = _hit()
    SourceRepository(database).add(replace(hit.source, id=None))
    privacy = PrivacyService(database)
    privacy.set_rule(1, PrivacyRule.LOCAL_MODEL_ONLY)
    gateway = FakeModelGateway()

    result = AnswerService(
        FakeRetriever((hit,)), ContextBuilder(), gateway, privacy
    ).ask("What does a TLB do?")

    assert result.text == NO_EVIDENCE_ANSWER
    assert result.citations == ()
    assert gateway.input_text is None


def test_answer_service_routes_private_evidence_to_local_model(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    hit = _hit()
    SourceRepository(database).add(replace(hit.source, id=None))
    privacy = PrivacyService(database)
    privacy.set_rule(1, PrivacyRule.LOCAL_MODEL_ONLY)
    cloud = FakeModelGateway(response="cloud")
    local = FakeModelGateway(response="local")

    result = AnswerService(
        FakeRetriever((hit,)), ContextBuilder(), cloud, privacy,
        ModelRouter(privacy, cloud, local),
    ).ask("What does a TLB do?")

    assert result.text == "local"
    assert cloud.input_text is None
    assert local.input_text is not None


def test_answer_service_explains_when_local_model_is_required_but_unavailable(tmp_path: Path) -> None:
    database = tmp_path / "steward.db"
    initialize_database(database)
    hit = _hit()
    SourceRepository(database).add(replace(hit.source, id=None))
    privacy = PrivacyService(database)
    privacy.set_rule(1, PrivacyRule.EXTERNAL_REDACTED)
    cloud = FakeModelGateway()

    result = AnswerService(
        FakeRetriever((hit,)), ContextBuilder(), cloud, privacy,
        ModelRouter(privacy, cloud),
    ).ask("What does a TLB do?")

    assert "does not permit an available model" in result.text
    assert cloud.input_text is None


def test_context_builder_deterministically_marks_truncated_evidence() -> None:
    hit = _hit()
    long_hit = HybridSearchHit(
        hit.source,
        SourceFragment(
            id=5,
            source_id=1,
            heading="Long note",
            ordinal=1,
            text="x" * 1_000,
            location="lines 13-50",
        ),
        0.02,
        None,
        0.7,
    )

    context = ContextBuilder(max_characters=360).build("Question?", (hit, long_hit))

    assert len(context.prompt) <= 360
    assert "[truncated]" in context.prompt
    assert [citation.key for citation in context.citations] == ["F1", "F2"]


def test_answer_service_rejects_an_empty_question() -> None:
    service = AnswerService(FakeRetriever(()), ContextBuilder(), FakeModelGateway())

    with pytest.raises(ValueError, match="Question must not be empty"):
        service.ask("   ")


def test_openai_gateway_uses_non_persisted_responses(monkeypatch) -> None:
    import openai

    recorded: dict[str, object] = {}

    class FakeResponses:
        @staticmethod
        def create(**kwargs):
            recorded.update(kwargs)
            return type("Response", (), {"output_text": "Grounded answer."})()

    class FakeClient:
        responses = FakeResponses()

    monkeypatch.setattr(openai, "OpenAI", lambda api_key: FakeClient())

    gateway = OpenAIModelGateway(api_key="test-key", model="test-model")

    assert gateway.generate(instructions="Use evidence.", input_text="[F1] note") == (
        "Grounded answer."
    )
    assert recorded == {
        "model": "test-model",
        "instructions": "Use evidence.",
        "input": "[F1] note",
        "store": False,
    }


def test_gemini_gateway_uses_non_persisted_interactions(monkeypatch) -> None:
    from google import genai

    recorded: dict[str, object] = {}

    class FakeInteractions:
        @staticmethod
        def create(**kwargs):
            recorded.update(kwargs)
            return type("Interaction", (), {"output_text": "Grounded answer."})()

    class FakeClient:
        interactions = FakeInteractions()

    monkeypatch.setattr(genai, "Client", lambda api_key: FakeClient())

    gateway = GeminiModelGateway(api_key="test-key", model="test-model")

    assert gateway.generate(instructions="Use evidence.", input_text="[F1] note") == (
        "Grounded answer."
    )
    assert recorded == {
        "model": "test-model",
        "system_instruction": "Use evidence.",
        "input": "[F1] note",
        "store": False,
    }
