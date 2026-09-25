from steward.answer import OllamaModelGateway


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
