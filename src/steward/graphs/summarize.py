"""Summarize: cache? → split → notes workers in parallel → combine → coverage → check → save."""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from steward.answer.gateway import ModelGateway
from steward.extraction import SourceFragment, SourceFragmentRepository
from steward.observability import trace
from steward.roles.checker import check_answer
from steward.roles.citations import KEY
from steward.roles.structured import CallBudget, StructuredOutputError
from steward.roles.summarize import combine_notes, write_notes
from steward.sources import Source, SourceRepository
from steward.sources.summaries import StoredSummary, SummaryRepository

BATCH_CHARACTERS = 24_000
SLICE_CHARACTERS = 20_000
MAX_BATCHES = 32
PARALLEL_WORKERS = 4


@dataclass(frozen=True, slots=True)
class Batch:
    index: int
    text: str
    keys: frozenset[str]
    locations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SummaryResult:
    status: str  # done | partial | notes_only | too_long | no_text | unavailable
    text: str = ""
    cited: tuple[tuple[str, str], ...] = ()  # (key, location)
    covered: int = 0
    total: int = 0
    skipped: tuple[str, ...] = ()
    calls: int = 0
    cached: bool = False


@dataclass
class SummarizeTools:
    sources: SourceRepository
    fragments: SourceFragmentRepository
    summaries: SummaryRepository
    model: ModelGateway | None
    model_name: str


class SummarizeState(TypedDict, total=False):
    source_id: int
    source: Source
    fragments: list[SourceFragment]
    budget: CallBudget
    batches: list[Batch]
    notes: Annotated[dict[int, str | None], operator.or_]
    summary: str
    notes_only: bool
    result: SummaryResult


class WorkerInput(TypedDict):
    batch: Batch
    budget: CallBudget


def build_summarize_graph(tools: SummarizeTools):
    def load(state: SummarizeState) -> dict:
        source = tools.sources.get_by_id(state["source_id"])
        fragments = list(tools.fragments.list_for_source(state["source_id"])) if source is not None else []
        if source is None or not fragments:
            return {"result": SummaryResult("no_text")}
        cached = tools.summaries.get(source.id or 0, source.content_hash, tools.model_name)
        if cached is not None:
            by_key = {f"F{fragment.id}": fragment.location for fragment in fragments}
            return {"result": SummaryResult(
                "done" if not cached.skipped else "partial", cached.text,
                tuple((key, by_key.get(key, "")) for key in cached.cited_keys),
                cached.covered, cached.total, cached.skipped, cached=True,
            )}
        if tools.model is None:
            return {"result": SummaryResult("unavailable")}
        batches = _split(fragments)
        if len(batches) > MAX_BATCHES:
            return {"result": SummaryResult("too_long", total=len(fragments))}
        # Each batch's notes may repair once; the combiner may repair once, plus one
        # coverage retry; up to three checker calls (15 sentences each) and one second
        # opinion on flagged sentences. A one-batch file skips the notes.
        budget = CallBudget(2 * len(batches) + 8 if len(batches) > 1 else 8)
        trace("summarize.split", batches=len(batches), sections=len(fragments))
        notes = {0: batches[0].text} if len(batches) == 1 else {}
        return {"source": source, "fragments": fragments, "batches": batches, "budget": budget, "notes": notes}

    def fan_out(state: SummarizeState) -> list[Send] | Literal["combine", "__end__"]:
        if "result" in state:
            return END
        if len(state["batches"]) == 1:
            return "combine"
        return [Send("notes", {"batch": batch, "budget": state["budget"]}) for batch in state["batches"]]

    def notes(worker: WorkerInput) -> dict:
        batch = worker["batch"]
        try:
            text: str | None = write_notes(tools.model, worker["budget"], batch=batch.text, keys=set(batch.keys))  # type: ignore[arg-type]
        except StructuredOutputError:
            trace("summarize.batch_skipped", batch=batch.index)
            text = None
        return {"notes": {batch.index: text}}

    def combine(state: SummarizeState) -> dict:
        budget, batches = state["budget"], state["batches"]
        kept = {index: text for index, text in state["notes"].items() if text}
        if not kept:
            return {"result": SummaryResult("unavailable", calls=budget.used)}
        allowed = set().union(*(batches[index].keys for index in kept))
        joined = "\n\n".join(kept[index] for index in sorted(kept))
        try:
            summary = combine_notes(tools.model, budget, notes=joined, keys=allowed)  # type: ignore[arg-type]
        except StructuredOutputError:
            trace("summarize.combine_failed")
            if len(batches) == 1:
                return {"result": SummaryResult("unavailable", calls=budget.used)}
            # Each batch's notes were already verified; show them rather than nothing.
            return {"summary": joined, "notes_only": True}
        gap = _largest_gap(state["fragments"], summary)
        if gap and budget.remaining >= 2:
            trace("summarize.coverage_retry", uncovered=len(gap))
            try:
                retry = combine_notes(
                    tools.model, budget, notes=joined, keys=allowed,  # type: ignore[arg-type]
                    cover="the sections at " + ", ".join(gap[:6]),
                )
                if _covered(state["fragments"], retry) > _covered(state["fragments"], summary):
                    summary = retry
            except StructuredOutputError:
                trace("summarize.coverage_retry_failed")  # keep the first summary
        return {"summary": summary, "notes_only": False}

    def check(state: SummarizeState) -> dict:
        fragments, budget = state["fragments"], state["budget"]
        if state["notes_only"]:
            status: str | None = "notes_only"
            text = state["summary"]
        else:
            status = None
            text = check_answer(
                tools.model, budget, state["summary"], {f"F{fragment.id}": fragment.text for fragment in fragments},
            ).text
        skipped = tuple(
            " – ".join((batch.locations[0], batch.locations[-1])) if len(batch.locations) > 1 else batch.locations[0]
            for batch in state["batches"] if not state["notes"].get(batch.index)
        )
        locations = {f"F{fragment.id}": fragment.location for fragment in fragments}
        cited = tuple((key, locations[key]) for key in dict.fromkeys(_keys(text)) if key in locations)
        status = status or ("partial" if skipped else "done")
        result = SummaryResult(
            status, text, cited, _covered(fragments, text), len(fragments), skipped, budget.used,
        )
        if status == "done":
            source = state["source"]
            tools.summaries.put(source.id or 0, source.content_hash, tools.model_name, StoredSummary(
                text, tuple(key for key, _ in cited), result.covered, result.total, skipped,
            ))
        trace("summarize.done", status=status, covered=result.covered, total=result.total, calls=budget.used)
        return {"result": result}

    def after_combine(state: SummarizeState) -> Literal["check", "__end__"]:
        return END if state.get("result") is not None else "check"

    graph = StateGraph(SummarizeState)
    graph.add_node("load", load)
    graph.add_node("notes", notes)
    graph.add_node("combine", combine)
    graph.add_node("check", check)
    graph.add_edge(START, "load")
    graph.add_conditional_edges("load", fan_out, ["notes", "combine", END])
    graph.add_edge("notes", "combine")
    graph.add_conditional_edges("combine", after_combine, ["check", END])
    graph.add_edge("check", END)
    return graph.compile()


def run_summarize(graph, source_id: int) -> SummaryResult:
    state = graph.invoke({"source_id": source_id}, {"max_concurrency": PARALLEL_WORKERS})
    return state["result"]


def _split(fragments: list[SourceFragment]) -> list[Batch]:
    batches: list[Batch] = []
    text, keys, locations = "", set(), []
    for fragment in fragments:
        for offset in range(0, max(1, len(fragment.text)), SLICE_CHARACTERS):
            unit = f"[F{fragment.id}] {fragment.location}\n{fragment.text[offset:offset + SLICE_CHARACTERS]}\n\n"
            if text and len(text) + len(unit) > BATCH_CHARACTERS:
                batches.append(Batch(len(batches), text, frozenset(keys), tuple(locations)))
                text, keys, locations = "", set(), []
            text += unit
            keys.add(f"F{fragment.id}")
            if fragment.location not in locations:
                locations.append(fragment.location)
    if text:
        batches.append(Batch(len(batches), text, frozenset(keys), tuple(locations)))
    return batches


def _keys(text: str) -> list[str]:
    return KEY.findall(text)


def _covered(fragments: list[SourceFragment], text: str) -> int:
    cited = set(_keys(text))
    return sum(f"F{fragment.id}" in cited for fragment in fragments)


def _largest_gap(fragments: list[SourceFragment], text: str) -> list[str]:
    """Locations of the longest run of uncited sections, if it's big enough to matter."""
    cited = set(_keys(text))
    best: list[str] = []
    run: list[str] = []
    for fragment in fragments:
        if f"F{fragment.id}" in cited:
            run = []
            continue
        run.append(fragment.location)
        if len(run) > len(best):
            best = list(run)
    threshold = max(3, len(fragments) // 5)
    return best if len(best) >= threshold else []

