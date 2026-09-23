"""Ask, Summarize, and the Checker they share."""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from steward.extraction import ExtractionResult, SourceFragment, SourceFragmentRepository
from steward.graphs.ask import AskTools, build_ask_graph, run_ask
from steward.graphs.summarize import SummarizeTools, build_summarize_graph, run_summarize
from steward.retrieval import LexicalSearchService
from steward.roles.checker import check_answer
from steward.roles.structured import CallBudget
from steward.roots import SourceRootRepository
from steward.sources import Source, SourceRepository, SourceType
from steward.sources.hashing import hash_file
from steward.sources.summaries import SummaryRepository
from steward.storage import initialize_database


class RoleModel:
    """Answers by role (identified from the prompt), so parallel call order doesn't matter."""

    def __init__(self, **responders) -> None:
        self.responders = responders
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def generate(self, *, instructions: str, input_text: str) -> str:
        role = next(name for name, marker in ROLE_MARKERS.items() if marker in instructions)
        with self._lock:
            self.calls.append(role)
        reply = self.responders[role](input_text)
        return reply if isinstance(reply, str) else json.dumps(reply)


ROLE_MARKERS = {
    "planner": "Plan searches",
    "answerer": "Answer only from the evidence",
    "checker": "For each numbered sentence",
    "notes": "Write concise notes",
    "combiner": "Write a clear summary",
}


class Library:
    def __init__(self, tmp_path: Path) -> None:
        database = tmp_path / "steward.db"
        initialize_database(database)
        self.root = tmp_path / "Y4S1"; self.root.mkdir()
        self.sources = SourceRepository(database)
        self.fragments = SourceFragmentRepository(database)
        self.roots = SourceRootRepository(database)
        self.roots.add("Y4S1", self.root)
        self.summaries = SummaryRepository(database)

    def add(self, name: str, *sections: str) -> tuple[Source, list[SourceFragment]]:
        path = self.root / name
        path.write_text("\n".join(sections), encoding="utf-8")
        now = datetime(2026, 9, 1, tzinfo=UTC)
        source = self.sources.add(Source(None, path.resolve(), hash_file(path), SourceType.MARKDOWN, 1, now, now, now))
        stored = self.fragments.replace_for_source(ExtractionResult(source.id or 0, tuple(
            SourceFragment(None, source.id or 0, None, index, text, f"slide {index + 1}") for index, text in enumerate(sections)
        )))
        return source, list(stored)

    def ask(self, model):
        return build_ask_graph(AskTools(
            self.sources, self.fragments, self.roots, LexicalSearchService(self.sources, self.fragments), model,
        ))

    def summarize(self, model):
        return build_summarize_graph(SummarizeTools(self.sources, self.fragments, self.summaries, model, "test-model"))


# -- Checker ---------------------------------------------------------------------

def test_checker_removes_unsupported_and_unknown_citations_and_keeps_formatting() -> None:
    evidence = {"F1": "Static scheduling divides loop iterations evenly between threads.",
                "F2": "Dynamic scheduling hands out chunks at runtime."}
    text = (
        "Static scheduling divides iterations evenly. [F1]\n"
        "- Dynamic scheduling hands out chunks at runtime [F2].\n"
        "It was invented in 1850 [F2].\n"
        "Guided scheduling is fastest [F9].\n"
        "In short, both are useful."
    )
    model = RoleModel(checker=lambda text: {"unsupported": [2]})

    result = check_answer(model, CallBudget(1), text, evidence)

    assert result.text == (
        "Static scheduling divides iterations evenly. [F1]\n"
        "- Dynamic scheduling hands out chunks at runtime [F2].\n\n"
        "In short, both are useful."
    )
    assert result.removed == 2 and result.checked == 4
    assert result.cited_keys == {"F1", "F2"}


def test_checker_without_budget_keeps_code_checked_sentences() -> None:
    result = check_answer(RoleModel(), CallBudget(0), "Threads share memory. [F1]", {"F1": "threads share memory"})

    assert result.text == "Threads share memory. [F1]" and result.removed == 0


# -- Ask -------------------------------------------------------------------------

def test_ask_plans_searches_answers_and_checks(tmp_path: Path) -> None:
    library = Library(tmp_path)
    _, fragments = library.add("openmp.md", "Static scheduling divides loop iterations evenly.")
    key = f"F{fragments[0].id}"
    model = RoleModel(
        planner=lambda _: {"searches": [{"query": "static scheduling", "root": "Y4S1", "types": []}]},
        answerer=lambda _: {"status": "answer", "text": f"It divides iterations evenly. [{key}]\nIt uses GPUs. [{key}]"},
        # The GPU sentence shares no words with its evidence, so code removes it
        # before the model check; the model sees and approves only the first.
        checker=lambda text: {"unsupported": []},
    )

    result = run_ask(library.ask(model), "what does static scheduling do?")

    assert result.status == "answered"
    assert result.text == f"It divides iterations evenly. [{key}]"
    assert result.removed == 1 and [item.key for item in result.cited] == [key]
    assert model.calls == ["planner", "answerer", "checker"]


def test_ask_can_request_one_more_search(tmp_path: Path) -> None:
    library = Library(tmp_path)
    _, first = library.add("static.md", "Static scheduling divides loop iterations evenly.")
    _, second = library.add("dynamic.md", "Dynamic scheduling hands out chunks at runtime.")
    replies = iter([
        {"status": "need_more", "query": "dynamic scheduling"},
        {"status": "answer", "text": f"Static splits evenly [F{first[0].id}]; dynamic hands out chunks [F{second[0].id}]."},
    ])
    model = RoleModel(
        planner=lambda _: {"searches": [{"query": "static", "root": None, "types": []}]},
        answerer=lambda _: next(replies),
        checker=lambda _: {"unsupported": []},
    )

    result = run_ask(library.ask(model), "compare static and dynamic scheduling")

    assert result.status == "answered" and len(result.cited) == 2
    assert model.calls == ["planner", "answerer", "answerer", "checker"]


def test_ask_about_one_file_skips_planning_and_uses_only_that_file(tmp_path: Path) -> None:
    library = Library(tmp_path)
    target, fragments = library.add("tut04.md", "AVX2 packs four doubles.")
    library.add("other.md", "AVX2 is also mentioned here.")
    seen: list[str] = []

    def answerer(evidence: str):
        seen.append(evidence)
        return {"status": "answer", "text": f"Four doubles. [F{fragments[0].id}]"}

    model = RoleModel(answerer=answerer, checker=lambda _: {"unsupported": []})

    result = run_ask(library.ask(model), "how many doubles?", source_id=target.id)

    assert result.status == "answered" and "planner" not in model.calls
    assert "tut04.md" in seen[0] and "other.md" not in seen[0]


def test_ask_without_evidence_makes_no_model_call(tmp_path: Path) -> None:
    library = Library(tmp_path)
    model = RoleModel(planner=lambda _: {"searches": [{"query": "zebra", "root": None, "types": []}]})

    result = run_ask(library.ask(model), "zebras?")

    assert result.status == "no_evidence" and model.calls == ["planner"]


def test_ask_answer_citing_unsupplied_keys_is_repaired_then_reported(tmp_path: Path) -> None:
    library = Library(tmp_path)
    library.add("openmp.md", "Static scheduling divides loop iterations evenly.")
    model = RoleModel(
        planner=lambda _: {"searches": [{"query": "static", "root": None, "types": []}]},
        answerer=lambda _: {"status": "answer", "text": "Made up [F999]."},
    )

    result = run_ask(library.ask(model), "static?")

    assert result.status == "unavailable" and result.cited
    assert model.calls == ["planner", "answerer", "answerer"]


# -- Summarize -------------------------------------------------------------------

def test_long_file_is_summarised_by_parallel_workers_then_cached(tmp_path: Path, monkeypatch) -> None:
    import steward.graphs.summarize as summarize

    monkeypatch.setattr(summarize, "BATCH_CHARACTERS", 60)
    library = Library(tmp_path)
    source, fragments = library.add("lecture.pptx", *(f"Slide about topic {index} and loops" for index in range(6)))
    keys = [f"F{fragment.id}" for fragment in fragments]

    def notes(batch: str) -> str:
        return " ".join(f"Topic noted [{key}]." for key in keys if f"[{key}]" in batch)

    model = RoleModel(
        notes=notes,
        combiner=lambda _: " ".join(f"The lecture covers topic loops [{key}]." for key in keys),
        checker=lambda _: {"unsupported": []},
    )

    result = run_summarize(library.summarize(model), source.id)

    assert result.status == "done" and result.covered == 6 and result.total == 6
    assert model.calls.count("notes") >= 2 and model.calls[-2:] == ["combiner", "checker"]
    calls_before = len(model.calls)
    again = run_summarize(library.summarize(model), source.id)
    assert again.cached and again.text == result.text and len(model.calls) == calls_before


def test_a_failing_batch_is_skipped_and_named_instead_of_failing_everything(tmp_path: Path, monkeypatch) -> None:
    import steward.graphs.summarize as summarize

    monkeypatch.setattr(summarize, "BATCH_CHARACTERS", 60)
    library = Library(tmp_path)
    source, fragments = library.add("lecture.pptx", *(f"Slide about topic {index} and loops" for index in range(4)))
    first_key = f"F{fragments[0].id}"

    def notes(batch: str) -> str:
        return f"Notes [{first_key}]." if f"[{first_key}]" in batch else "no citations here"

    model = RoleModel(
        notes=notes,
        combiner=lambda _: f"The lecture opens with topic loops [{first_key}].",
        checker=lambda _: {"unsupported": []},
    )

    result = run_summarize(library.summarize(model), source.id)

    assert result.status == "partial" and result.skipped
    assert result.cited == ((first_key, "slide 1"),)
    assert library.summaries.get(source.id, source.content_hash, "test-model") is None


def test_short_file_needs_one_summary_call_and_no_workers(tmp_path: Path) -> None:
    library = Library(tmp_path)
    source, fragments = library.add("note.md", "OpenMP loops run in parallel.")
    model = RoleModel(
        combiner=lambda _: f"Loops run in parallel. [F{fragments[0].id}]",
        checker=lambda _: {"unsupported": []},
    )

    result = run_summarize(library.summarize(model), source.id)

    assert result.status == "done" and model.calls == ["combiner", "checker"]
