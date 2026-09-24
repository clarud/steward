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


def test_ask_answer_citing_many_unsupplied_keys_is_repaired_then_reported(tmp_path: Path) -> None:
    library = Library(tmp_path)
    library.add("openmp.md", "Static scheduling divides loop iterations evenly.")
    model = RoleModel(
        planner=lambda _: {"searches": [{"query": "static"}]},
        answerer=lambda _: {"status": "answer", "text": "Made up [F997]. Also [F998]. And [F999]."},
    )

    result = run_ask(library.ask(model), "static?")

    assert result.status == "unavailable" and result.cited
    assert model.calls == ["planner", "answerer", "answerer"]


def test_one_invented_key_is_left_for_the_checker_to_remove(tmp_path: Path) -> None:
    library = Library(tmp_path)
    library.add("openmp.md", "Static scheduling divides loop iterations evenly.")
    model = RoleModel(
        planner=lambda _: {"searches": [{"query": "static"}]},
        answerer=lambda _: {"status": "answer", "text": "Made up [F999]."},
    )

    result = run_ask(library.ask(model), "static?")

    assert result.status == "unreliable"  # the checker removed the only cited sentence
    assert model.calls == ["planner", "answerer"]


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


# -- Fixes found by the real-data evaluation (24 Sep 2026) ------------------------

def test_citation_lists_and_labels_are_normalised_before_checking() -> None:
    from steward.roles.citations import normalize

    assert normalize("A [F1, F2] and B [Fn: F9] and [a link](x) [F3; F4]") == "A [F1][F2] and B [F9] and [a link](x) [F3][F4]"
    result = check_answer(None, CallBudget(0), "Warps have 32 threads [F1, F2].", {"F1": "warps have 32 threads", "F2": "x"})
    assert result.cited_keys == {"F1", "F2"} and result.checked == 1


def test_checker_does_not_split_decimals_or_abbreviations() -> None:
    evidence = {"F1": "The serial run took 0.25 s, e.g. on the i7 machine, with r.v. inputs."}
    text = "The serial run took 0.25 s, e.g. on the i7 machine [F1]. Nothing else."

    result = check_answer(None, CallBudget(0), text, evidence)

    assert result.text == text and result.checked == 1 and result.removed == 0


def test_ask_planner_guesses_never_filter_the_search(tmp_path: Path) -> None:
    library = Library(tmp_path)
    _, notes = library.add("heritage.md", "The state's approach to heritage conservation changed in the 1980s.")
    model = RoleModel(
        # A planner that guesses a folder and type that would hide the file.
        planner=lambda _: {"searches": [{"query": "heritage conservation", "root": "Elsewhere", "types": ["pdf"]}]},
        answerer=lambda _: {"status": "answer", "text": f"It changed in the 1980s [F{notes[0].id}]."},
        checker=lambda _: {"unsupported": []},
    )

    result = run_ask(library.ask(model), "what is the state's approach to heritage conservation")

    assert result.status == "answered" and result.cited[0].fragment.id == notes[0].id


def test_ask_says_the_files_dont_answer_when_evidence_is_still_missing(tmp_path: Path) -> None:
    library = Library(tmp_path)
    library.add("coherence.md", "Cache coherence keeps copies of shared data consistent.")
    model = RoleModel(
        planner=lambda _: {"searches": [{"query": "cache coherence"}]},
        answerer=lambda _: {"status": "need_more", "query": "MESI write miss transitions"},
    )

    result = run_ask(library.ask(model), "what are the MESI transitions on a write miss in coherence")

    assert result.status == "not_found"
    assert "don't seem to answer" in result.text
    # one extra search, one retry asking for an answer from what's there, then an honest reply
    assert model.calls == ["planner", "answerer", "answerer", "answerer"]


def test_a_retry_answers_from_the_evidence_instead_of_giving_up(tmp_path: Path) -> None:
    library = Library(tmp_path)
    _, notes = library.add("coherence.md", "Cache coherence keeps copies of shared data consistent.")
    replies = iter([
        {"status": "need_more", "query": "more coherence"},
        {"status": "need_more", "query": "even more"},
        {"status": "answer", "text": f"Coherence keeps shared copies consistent [F{notes[0].id}]."},
    ])
    model = RoleModel(
        planner=lambda _: {"searches": [{"query": "cache coherence"}]},
        answerer=lambda _: next(replies), checker=lambda _: {"unsupported": []},
    )

    result = run_ask(library.ask(model), "what does cache coherence do")

    assert result.status == "answered" and "consistent" in result.text


def test_overlong_notes_with_label_citations_are_trimmed_not_rejected() -> None:
    from steward.roles.summarize import NOTE_LIMIT, write_notes

    long_notes = "\n".join(f"- Point {index} about loops [Fn: F7]" for index in range(400))
    model = RoleModel(notes=lambda _: long_notes)

    notes = write_notes(model, CallBudget(2), batch="[F7] slide 1\nloops", keys={"F7"})

    assert len(notes) <= NOTE_LIMIT and notes.endswith("[F7]")
    assert model.calls == ["notes"]


def test_ask_follows_a_title_slide_with_the_slides_after_it(tmp_path: Path) -> None:
    library = Library(tmp_path)
    _, slides = library.add(
        "heritage.pptx.md", "THE STATE'S APPROACH TO HERITAGE CONSERVATION",
        "1960s to mid-1980s: urban renewal pragmatism came first.", "Mid-1980s onward: heritage awareness grew.",
        "Unrelated closing slide.",
    )
    seen: list[str] = []

    def answer(evidence: str):
        seen.append(evidence)
        return {"status": "answer", "text": f"Urban renewal came first [F{slides[1].id}]."}

    model = RoleModel(
        planner=lambda _: {"searches": [{"query": "state approach heritage conservation"}]},
        answerer=answer, checker=lambda _: {"unsupported": []},
    )

    result = run_ask(library.ask(model), "what is the state's approach to heritage conservation")

    assert result.status == "answered"
    assert f"[F{slides[1].id}]" in seen[0] and f"[F{slides[2].id}]" in seen[0]
    assert f"[F{slides[3].id}]" not in seen[0]


def test_a_line_of_bare_citations_stays_with_the_text_before_it() -> None:
    evidence = {"F1": "Poisson arrivals have exponential inter-arrival times.", "F2": "The M/M/1 queue is stable if rho < 1."}
    text = "Poisson arrivals have exponential inter-arrival times, and M/M/1 is stable if rho < 1.\n[F1][F2]"

    result = check_answer(None, CallBudget(0), text, evidence)

    assert result.text == text and result.cited_keys == {"F1", "F2"} and result.removed == 0


def test_combiner_repairs_a_thinly_cited_summary_once() -> None:
    from steward.roles.summarize import combine_notes

    uncited = "\n\n".join(["### Topic\nThis paragraph explains an important idea at considerable length but gives no key at all."] * 3)
    cited = "### Topic\nThis paragraph explains an important idea at some length, with its key [F1]."
    replies = iter([uncited + "\n\nOne cited paragraph that is long enough to count here [F1].", cited])
    model = RoleModel(combiner=lambda _: next(replies))

    assert combine_notes(model, CallBudget(3), notes="[F1] notes", keys={"F1"}) == cited
    assert model.calls == ["combiner", "combiner"]


def test_checker_judges_long_texts_in_groups_within_the_budget() -> None:
    evidence = {f"F{index}": f"Fact number {index} concerns loops" for index in range(40)}
    text = "\n".join(f"Fact number {index} concerns loops [F{index}]." for index in range(40))
    groups: list[str] = []

    def judge(listing: str):
        groups.append(listing)
        return {"unsupported": []}

    result = check_answer(RoleModel(checker=judge), CallBudget(2), text, evidence)

    assert len(groups) == 2 and groups[0].count("\n#") == 14 and result.removed == 0


def test_one_invented_key_is_left_for_the_checker_but_many_are_rejected() -> None:
    from steward.roles.summarize import _problem

    assert _problem("A [F1]. B [F2]. C [F3]. D [F4]. E [F5]. F [F99].", {"F1", "F2", "F3", "F4", "F5"}) == ""
    assert _problem("A [F1]. B [F97]. C [F98]. D [F99].", {"F1"}).startswith("cites keys from outside")


def test_second_opinion_keeps_a_sentence_the_full_section_supports() -> None:
    long_section = ("Filler about scheduling policies. " * 40) + "A warp has 32 threads."
    verdicts = iter([{"unsupported": [0, 1]}, {"unsupported": [1]}])
    seen: list[str] = []

    def judge(listing: str):
        seen.append(listing)
        return next(verdicts)

    text = "A warp has 32 threads [F1].\nA warp has 64 threads [F1]."
    result = check_answer(RoleModel(checker=judge), CallBudget(4), text, {"F1": long_section})

    assert result.text == "A warp has 32 threads [F1]."
    assert result.removed_sentences == ("A warp has 64 threads [F1].",)
    assert "A warp has 32 threads." in seen[1]  # the second opinion saw the whole section


def test_without_a_second_verdict_the_first_one_stands() -> None:
    result = check_answer(
        RoleModel(checker=lambda _: {"unsupported": [0]}), CallBudget(1),
        "Threads share memory [F1].", {"F1": "threads share memory"},
    )

    assert result.removed == 1 and result.text == ""


def test_ask_shows_what_survives_and_withholds_only_when_nothing_cited_does(tmp_path: Path) -> None:
    library = Library(tmp_path)
    _, notes = library.add("amdahl.md", "Amdahl: speedup is bounded by 1/f for serial fraction f.")
    key = f"F{notes[0].id}"
    answer = {"status": "answer", "text": f"Speedup is bounded by 1/f [{key}].\nSpeedup is unlimited [{key}].\nIt is bounded [{key}]."}
    two_of_three = RoleModel(
        planner=lambda _: {"searches": [{"query": "Amdahl speedup"}]}, answerer=lambda _: answer,
        checker=lambda _: {"unsupported": [1, 2]},
    )

    partial = run_ask(library.ask(two_of_three), "what does Amdahl say about speedup")

    assert partial.status == "answered" and partial.removed == 2
    assert partial.text == f"Speedup is bounded by 1/f [{key}]."

    everything = RoleModel(
        planner=lambda _: {"searches": [{"query": "Amdahl speedup"}]}, answerer=lambda _: answer,
        checker=lambda _: {"unsupported": [0, 1, 2]},
    )
    assert run_ask(library.ask(everything), "what does Amdahl say about speedup").status == "unreliable"


def test_no_second_look_when_the_first_saw_the_whole_section() -> None:
    model = RoleModel(checker=lambda _: {"unsupported": [0]})

    result = check_answer(model, CallBudget(4), "Valgrind cannot detect races [F1].", {"F1": "Valgrind detects race conditions."})

    assert result.removed == 1 and model.calls == ["checker"]


def test_a_citation_to_a_heading_is_repointed_to_the_section_that_says_it() -> None:
    from steward.roles.checker import realign_citations

    evidence = {
        "F1": "## Data and task parallelism",
        "F2": "Data parallelism partitions the dataset across processing units; task parallelism partitions the computation.",
    }
    text = "Data parallelism partitions the dataset across processing units [F1]."

    assert realign_citations(text, evidence) == "Data parallelism partitions the dataset across processing units [F2]."
    result = check_answer(None, CallBudget(0), text, evidence)
    assert result.removed == 0 and result.cited_keys == {"F2"}


def test_an_invented_statement_keeps_its_citation_and_is_still_removed() -> None:
    from steward.roles.checker import realign_citations

    evidence = {"F1": "The assignment is due on Friday 18 September.", "F2": "Tutorials run on Mondays."}
    text = "Quantum annealing outperforms classical hardware [F1]."

    assert realign_citations(text, evidence) == text
    assert check_answer(None, CallBudget(0), text, evidence).removed == 1


def test_a_correct_citation_is_left_alone() -> None:
    from steward.roles.checker import realign_citations

    evidence = {"F1": "Warps have 32 threads on NVIDIA GPUs.", "F2": "Warps have 32 threads; blocks run on one SM."}
    text = "Warps have 32 threads on NVIDIA GPUs [F1]."

    assert realign_citations(text, evidence) == text


def test_an_uncited_answer_is_repaired_and_an_uncited_decline_means_not_found(tmp_path: Path) -> None:
    library = Library(tmp_path)
    _, notes = library.add("padding.md", "Padding arrays keeps each thread's data on its own cache line, avoiding false sharing.")
    replies = iter([
        {"status": "answer", "text": "Padding avoids false sharing."},  # no citation: repaired
        {"status": "answer", "text": f"Padding avoids false sharing [F{notes[0].id}]."},
    ])
    model = RoleModel(
        planner=lambda _: {"searches": [{"query": "padding false sharing"}]},
        answerer=lambda _: next(replies), checker=lambda _: {"unsupported": []},
    )

    assert run_ask(library.ask(model), "why pad arrays").status == "answered"

    declining = RoleModel(
        planner=lambda _: {"searches": [{"query": "padding false sharing"}]},
        answerer=lambda _: {"status": "answer", "text": "The provided evidence does not mention Kubernetes."},
    )
    assert run_ask(library.ask(declining), "what does kubernetes do with padding").status == "not_found"


def test_the_extra_search_looks_deeper_inside_files_already_found(tmp_path: Path) -> None:
    library = Library(tmp_path)
    sections = [f"GPU slide {index} about streaming multiprocessors and warps." for index in range(12)]
    sections.append("The H100 has 144 SMs sharing an L2 cache.")
    _, slides = library.add("gpu.md", *sections)
    seen: list[str] = []

    def answer(evidence: str):
        seen.append(evidence)
        if len(seen) == 1:
            return {"status": "need_more", "query": "H100 SMs"}
        return {"status": "answer", "text": f"The H100 has 144 SMs [F{slides[-1].id}]."}

    model = RoleModel(
        planner=lambda _: {"searches": [{"query": "streaming multiprocessors"}]},
        answerer=answer, checker=lambda _: {"unsupported": []},
    )

    result = run_ask(library.ask(model), "how many SMs does the H100 have")

    assert result.status == "answered"
    assert seen[1].split("Evidence:\n")[1].startswith(f"[F{slides[-1].id}]")  # the requested section comes first
