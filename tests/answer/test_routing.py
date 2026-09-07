from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from steward.answer import ModelLocation, ModelRouter, ModelRoutingError, OllamaModelGateway
from steward.privacy import PrivacyRule, PrivacyService
from steward.sources import Source, SourceRepository, SourceType
from steward.storage import initialize_database


@dataclass
class FakeGateway:
    name: str

    def generate(self, *, instructions: str, input_text: str) -> str:
        return self.name


def _privacy_with_source(tmp_path: Path) -> PrivacyService:
    database = tmp_path / "steward.db"
    initialize_database(database)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    SourceRepository(database).add(
        Source(None, tmp_path / "note.md", "a" * 64, SourceType.MARKDOWN, 0, now, now, now)
    )
    return PrivacyService(database)


def test_model_router_uses_cloud_for_externally_allowed_content(tmp_path: Path) -> None:
    privacy = _privacy_with_source(tmp_path)
    cloud = FakeGateway("cloud")

    selection = ModelRouter(privacy, cloud, FakeGateway("local")).select([1])

    assert selection.gateway is cloud
    assert selection.location is ModelLocation.CLOUD


def test_model_router_requires_local_gateway_for_private_content(tmp_path: Path) -> None:
    privacy = _privacy_with_source(tmp_path)
    privacy.set_rule(1, PrivacyRule.LOCAL_MODEL_ONLY)

    with pytest.raises(ModelRoutingError, match="local model"):
        ModelRouter(privacy, FakeGateway("cloud")).select([1])

    local = FakeGateway("local")
    selection = ModelRouter(privacy, FakeGateway("cloud"), local).select([1])
    assert selection.gateway is local
    assert selection.location is ModelLocation.LOCAL


def test_model_router_rejects_no_model_content(tmp_path: Path) -> None:
    privacy = _privacy_with_source(tmp_path)
    privacy.set_rule(1, PrivacyRule.NO_MODEL)

    assert ModelRouter(privacy, FakeGateway("cloud")).allows_any_model(1) is False
    with pytest.raises(ValueError, match="filtered"):
        ModelRouter(privacy, FakeGateway("cloud")).select([1])


def test_ollama_gateway_keeps_request_local(monkeypatch) -> None:
    recorded: dict[str, object] = {}

    class FakeResponse:
        def read(self) -> bytes:
            return b'{"response":"Local answer"}'

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(http_request, timeout: int):
        recorded["url"] = http_request.full_url
        recorded["body"] = http_request.data
        recorded["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("steward.answer.gateway.request.urlopen", fake_urlopen)

    result = OllamaModelGateway(model="llama3", base_url="http://127.0.0.1:11434").generate(
        instructions="Ground from evidence.", input_text="[F1] private note"
    )

    assert result == "Local answer"
    assert recorded["url"] == "http://127.0.0.1:11434/api/generate"
    assert b'"stream": false' in recorded["body"]
