"""Find: Planner and Judge contracts, fallbacks, reformulation, and the call budget."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

from steward.extraction import ExtractionResult, SourceFragment, SourceFragmentRepository
from steward.graphs.find import FindScope, FindTools, build_find_graph, run_find
from steward.retrieval import LexicalSearchService
from steward.roots import SourceRootRepository
from steward.sources import Source, SourceRepository, SourceType
from steward.sources.hashing import hash_file
from steward.storage import initialize_database


class ScriptedModel:
    """Returns queued replies in order and records what each role was asked."""

    def __init__(self, *replies: object) -> None:
        self.replies = [json.dumps(item) if not isinstance(item, str) else item for item in replies]
        self.prompts: list[str] = []

    def generate(self, *, instructions: str, input_text: str) -> str:
        self.prompts.append(input_text)
        if not self.replies:
            raise AssertionError("the flow made more model calls than scripted")
        return self.replies.pop(0)


class Library:
    def __init__(self, tmp_path: Path) -> None:
        self.database = tmp_path / "steward.db"
        initialize_database(self.database)
        self.root_path = tmp_path / "Y4S1"
        self.root_path.mkdir()
        self.sources = SourceRepository(self.database)
        self.fragments = SourceFragmentRepository(self.database)
        self.roots = SourceRootRepository(self.database)
        self.roots.add("Y4S1", self.root_path)
        self.files: dict[str, Source] = {}

    def add(self, relative: str, text: str, *, modified: datetime | None = None) -> Source:
        path = self.root_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        when = modified or datetime(2026, 9, 1, tzinfo=UTC)
        source = self.sources.add(Source(None, path.resolve(), hash_file(path), SourceType.MARKDOWN, 1, when, when, when))
        self.fragments.replace_for_source(ExtractionResult(source.id or 0, (
            SourceFragment(None, source.id or 0, None, 0, text, "lines 1-1"),
        )))
        self.files[relative] = source
        return source

    def graph(self, model: ScriptedModel | None):
        return build_find_graph(FindTools(
            sources=self.sources, roots=self.roots,
            lexical=LexicalSearchService(self.sources, self.fragments), semantic=None, model=model,
            location=lambda source: str(source.path.relative_to(self.root_path.resolve())),
            today=lambda: date(2026, 9, 24),
        ))


def _plan(**overrides: object) -> dict:
    plan = {"keywords": ["AVX"], "meaning_query": "vector registers", "types": [], "root": None,
            "folder_hint": None, "since": None, "filename_hint": None}
    plan.update(overrides)
    return plan


def test_planner_and_judge_pick_the_file_with_a_reason(tmp_path: Path) -> None:
    library = Library(tmp_path)
    tutorial = library.add("CS3210/Tutorials/tut04.md", "AVX2 packs four doubles in a register")
    library.add("CS4226/queueing.md", "Little's law for queues")
    model = ScriptedModel(
        _plan(keywords=["AVX2", "register"], root="Y4S1", folder_hint="Tutorials"),
        {"decision": "picks", "picks": [{"id": tutorial.id, "reason": "Tutorial 4 on AVX2 registers"}]},
    )

    result = run_find(library.graph(model), "that AVX question from tut 4")

    assert result.kind == "picks"
    assert [(candidate.source.id, reason) for candidate, reason in result.picks] == [(tutorial.id, "Tutorial 4 on AVX2 registers")]
    assert result.calls == 2
    assert "Allowed roots and their folders:\n- Y4S1: CS3210, CS4226" in model.prompts[0]
    assert "CS3210" in model.prompts[1] and "AVX2 packs" in model.prompts[1]


def test_invalid_planner_output_is_repaired_once(tmp_path: Path) -> None:
    library = Library(tmp_path)
    tutorial = library.add("CS3210/tut04.md", "AVX2 registers")
    model = ScriptedModel(
        "sure, here's a plan!",
        _plan(keywords=["AVX2"]),
        {"decision": "picks", "picks": [{"id": tutorial.id, "reason": "AVX2"}]},
    )

    result = run_find(library.graph(model), "avx")

    assert result.kind == "picks" and result.calls == 3
    assert "Your previous reply was rejected: no JSON object found" in model.prompts[1]


def test_judge_cannot_invent_a_file_and_falls_back_to_the_ranking(tmp_path: Path) -> None:
    library = Library(tmp_path)
    tutorial = library.add("CS3210/tut04.md", "AVX2 registers")
    invented = {"decision": "picks", "picks": [{"id": 999, "reason": "made up"}]}
    model = ScriptedModel(_plan(keywords=["AVX2"]), invented, invented)

    result = run_find(library.graph(model), "avx")

    assert [candidate.source.id for candidate, _ in result.picks] == [tutorial.id]
    assert result.picks[0][1] == "matched text at lines 1-1"
    assert result.calls == 3


def test_an_empty_first_search_is_reformulated_once(tmp_path: Path) -> None:
    library = Library(tmp_path)
    notes = library.add("CS4226/queueing.md", "Little's law relates arrivals and waiting time")
    model = ScriptedModel(
        _plan(keywords=["zzz-nothing"], types=["pdf"]),
        _plan(keywords=["Little", "waiting"]),
        {"decision": "picks", "picks": [{"id": notes.id, "reason": "Little's law notes"}]},
    )

    result = run_find(library.graph(model), "the waiting time formula")

    assert result.kind == "picks" and result.picks[0][0].source.id == notes.id
    assert "found nothing suitable" in model.prompts[1]
    assert result.calls == 3


def test_judge_can_ask_which_of_two_files(tmp_path: Path) -> None:
    library = Library(tmp_path)
    first = library.add("CS3210/tut04.md", "tutorial 4 exercises")
    second = library.add("CS4226/tut04.md", "tutorial 4 exercises")
    model = ScriptedModel(
        _plan(keywords=["tutorial"]),
        {"decision": "clarify", "question": "CS3210 or CS4226 tutorial 4?", "options": [first.id, second.id]},
    )

    result = run_find(library.graph(model), "tutorial 4")

    assert result.kind == "clarify" and result.question == "CS3210 or CS4226 tutorial 4?"
    assert {candidate.source.id for candidate in result.options} == {first.id, second.id}


def test_without_a_model_find_uses_the_request_as_written(tmp_path: Path) -> None:
    library = Library(tmp_path)
    notes = library.add("CS4226/queueing.md", "Little's law")

    result = run_find(library.graph(None), "Little law")

    assert result.kind == "picks" and result.picks[0][0].source.id == notes.id and result.calls == 0


def test_recent_and_explicit_filters_narrow_candidates(tmp_path: Path) -> None:
    library = Library(tmp_path)
    old = library.add("CS3210/old.md", "slides", modified=datetime(2026, 8, 1, tzinfo=UTC))
    new = library.add("CS3210/new.md", "slides", modified=datetime(2026, 9, 20, tzinfo=UTC))
    model = ScriptedModel(
        _plan(keywords=["nothing-matches-this"], since="2026-09-17", types=["pdf"]),
        {"decision": "picks", "picks": [{"id": new.id, "reason": "added this week"}]},
    )

    result = run_find(library.graph(model), "what did I add this week", scope=FindScope(types=(SourceType.MARKDOWN,)))

    assert [candidate.source.id for candidate, _ in result.picks] == [new.id]
    assert str(old.id) not in model.prompts[-1].split("Candidates:")[1].split(":")[0]
    assert "only markdown" in result.tried


def test_a_wrong_planner_guess_only_lowers_the_rank(tmp_path: Path) -> None:
    library = Library(tmp_path)
    other_root = tmp_path / "Telegram Test"
    other_root.mkdir()
    library.roots.add("Telegram Test", other_root)
    target = library.add("CS2106/virtual-memory.md", "the TLB caches page table entries for address translation")
    model = ScriptedModel(
        _plan(keywords=["TLB"], root="Telegram Test", types=["pdf"], folder_hint="CS4226"),
        {"decision": "no_match"},
        _plan(keywords=["TLB", "translation"]),
        {"decision": "no_match"},
    )

    result = run_find(library.graph(model), "the cache CPUs use for address translation")

    assert result.kind == "closest"
    assert result.picks[0][0].source.id == target.id


def test_a_failing_model_never_exceeds_the_budget(tmp_path: Path) -> None:
    library = Library(tmp_path)
    library.add("CS3210/tut04.md", "AVX2 registers")
    model = ScriptedModel(*(["not json"] * 4))

    result = run_find(library.graph(model), "AVX2 registers")

    assert result.calls == 4
    assert result.kind == "picks"  # plain ranking still answers
