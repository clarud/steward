from pathlib import Path
import pytest

from steward.answer.document import DocumentSynthesisError, synthesize_long_document
from steward.answer.models import AnswerCitation
from steward.extraction.models import SourceFragment


def test_long_document_reads_last_section_and_synthesizes_only_verified_notes():
    parts = [SourceFragment(1, 1, None, 0, "a" * 65000 + "LAST_SECTION_MARKER", "page 1")]
    citations = [AnswerCitation("F1", 1, Path("document.pdf"), None, "page 1")]

    class Model:
        def __init__(self):
            self.inputs = []

        def generate(self, *, instructions, input_text):
            self.inputs.append(input_text)
            return "Summary with evidence [F1]."

    model = Model()
    assert "[F1]" in synthesize_long_document(model, parts, citations)
    assert len(model.inputs) == 5
    assert any("LAST_SECTION_MARKER" in text for text in model.inputs[:-1])
    assert "Notes from every batch" in model.inputs[-1]


@pytest.mark.parametrize("response", ["No citations", "Unknown [F9]", "x" * 1601 + " [F1]"])
def test_bad_batch_never_produces_partial_document_answer(response):
    class Model:
        calls = 0
        def generate(self, **kwargs):
            self.calls += 1
            return response
    model = Model()
    with pytest.raises(DocumentSynthesisError, match="No partial summary"):
        synthesize_long_document(model, [SourceFragment(1, 1, None, 0, "x" * 65000, "page 1")], [AnswerCitation("F1", 1, Path("a.pdf"), None, "page 1")])
    assert model.calls == 1


def test_batch_budget_is_checked_before_any_model_request():
    class Model:
        def generate(self, **kwargs):
            raise AssertionError("Budget refusal must precede model calls")
    with pytest.raises(DocumentSynthesisError, match="32-batch"):
        synthesize_long_document(Model(), [SourceFragment(1, 1, None, 0, "x" * 700000, "page 1")], [])


def test_combination_cannot_cite_a_key_not_retained_in_batch_notes():
    class Model:
        calls = 0
        def generate(self, **kwargs):
            self.calls += 1
            return "Notes [F1]" if self.calls == 1 else "Invented reference [F2]"
    with pytest.raises(DocumentSynthesisError, match="combined answer"):
        synthesize_long_document(Model(), [SourceFragment(1, 1, None, 0, "text", "page 1")], [AnswerCitation("F1", 1, Path("a.pdf"), None, "page 1"), AnswerCitation("F2", 2, Path("a.pdf"), None, "page 2")])
