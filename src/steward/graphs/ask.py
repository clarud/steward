"""Ask: plan → gather evidence → answer (→ one more search) → check → reply."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from steward.answer.gateway import ModelGateway
from steward.extraction import SourceFragment, SourceFragmentRepository
from steward.observability import trace
from steward.roles.ask import Draft, SearchSpec, draft_answer, plan_searches
from steward.roles.checker import check_answer
from steward.roles.structured import CallBudget, StructuredOutputError
from steward.roots import SourceRootRepository
from steward.sources import Source, SourceRepository, SourceType

# plan, answer, one more answer after an extra search (or one retry instead of
# giving up), check, and the check's second opinion
ASK_BUDGET = 6
EVIDENCE_CHARACTERS = 12_000
PER_SEARCH = 6
# A matched section shorter than this (a title slide, a heading) brings the sections after it.
SHORT_SECTION = 300
NEIGHBOURS = 2
# A section this short (a bare heading, a file's header line) is replaced by the
# sections after it, so the writer can't cite a heading for a fact.
TINY_SECTION = 200
# An extra search also looks inside the files the first search found, up to this many.
DEEPER_FILES = 2


class Retriever(Protocol):
    def search(
        self, query: str, *, limit: int = 5, source_types: tuple[SourceType, ...] | None = None,
        path_prefix: Path | None = None,
    ) -> tuple[object, ...]: ...


@dataclass(frozen=True, slots=True)
class Evidence:
    key: str
    source: Source
    fragment: SourceFragment


@dataclass(frozen=True, slots=True)
class AskResult:
    status: str  # answered | no_evidence | not_found | unavailable | unreliable
    text: str
    cited: tuple[Evidence, ...] = ()
    removed: int = 0
    calls: int = 0
    removed_text: tuple[str, ...] = ()


@dataclass
class AskTools:
    sources: SourceRepository
    fragments: SourceFragmentRepository
    roots: SourceRootRepository
    retriever: Retriever
    model: ModelGateway | None


class AskState(TypedDict, total=False):
    question: str
    source_id: int | None
    previous: str | None
    budget: CallBudget
    searches: tuple[SearchSpec, ...]
    evidence: list[Evidence]
    extra_query: str | None
    draft: Draft
    result: AskResult


def build_ask_graph(tools: AskTools):
    def plan(state: AskState) -> dict:
        budget = CallBudget(ASK_BUDGET if tools.model is not None else 0)
        searches: tuple[SearchSpec, ...] = (SearchSpec(state["question"]),)
        if tools.model is not None and state.get("source_id") is None:
            try:
                searches = plan_searches(
                    tools.model, budget, question=state["question"],
                    roots=[root.name for root in tools.roots.list_all()], previous=state.get("previous"),
                )
            except StructuredOutputError:
                trace("ask.plan_fallback")
        trace("ask.plan", searches=len(searches), calls=budget.used)
        return {"budget": budget, "searches": searches, "evidence": [], "extra_query": None}

    def gather(state: AskState) -> dict:
        evidence = list(state.get("evidence", []))
        specs = (SearchSpec(state["extra_query"]),) if state.get("extra_query") else state["searches"]
        # The extra search also looks deeper inside the files already found: the
        # right file is usually there, just not the right section.
        within = tuple(dict.fromkeys(item.source.path for item in evidence))[:DEEPER_FILES] if state.get("extra_query") else ()
        found: list[Evidence] = []
        for fragment, source in _retrieve(tools, specs, state.get("source_id"), within):
            if all(item.fragment.id != fragment.id for item in evidence + found):
                found.append(Evidence(f"F{fragment.id}", source, fragment))
        # What the writer asked for goes first, so the evidence cap never cuts it off.
        evidence = found + evidence if state.get("extra_query") else evidence + found
        trace("ask.gather", sections=len(evidence))
        return {"evidence": _fit(evidence)}

    def answer(state: AskState) -> dict:
        evidence = state["evidence"]
        budget = state["budget"]
        if tools.model is None:
            return {"draft": Draft("answer", text="")}
        allow_more = state.get("extra_query") is None and state.get("source_id") is None and budget.remaining >= 3
        try:
            draft = draft_answer(
                tools.model, budget, question=state["question"], evidence=_render(evidence),
                keys={item.key for item in evidence}, allow_more=allow_more,
            )
        except StructuredOutputError:
            trace("ask.answer_unavailable")
            draft = Draft("answer", text="")
        trace("ask.answer", status=draft.status, calls=budget.used)
        return {"draft": draft, "extra_query": draft.query or state.get("extra_query")}

    def check(state: AskState) -> dict:
        draft, evidence, budget = state["draft"], state["evidence"], state["budget"]
        if draft.status == "not_found":
            return {"result": AskResult(
                "not_found", "Your files don't seem to answer that. These are the closest matches:",
                tuple(evidence[:5]), calls=budget.used,
            )}
        if not draft.text:
            return {"result": AskResult(
                "unavailable", "The model couldn't answer right now. These files look relevant:",
                tuple(evidence[:5]), calls=budget.used,
            )}
        checked = check_answer(tools.model, budget, draft.text, {item.key: item.fragment.text for item in evidence})
        cited = tuple(item for item in evidence if item.key in checked.cited_keys)
        # Show whatever survived the check, with a count of what was removed. Withhold
        # only when no cited statement survived: then nothing left is grounded.
        if checked.checked and not checked.cited_keys:
            status, text = "unreliable", "I couldn't answer that reliably from your files. These look relevant:"
            cited = tuple(evidence[:5])
        else:
            status, text = "answered", checked.text
        trace("ask.check", removed=checked.removed, checked=checked.checked, calls=budget.used)
        return {"result": AskResult(status, text, cited, checked.removed, budget.used, checked.removed_sentences)}

    def no_evidence(state: AskState) -> dict:
        return {"result": AskResult(
            "no_evidence", "I couldn't find anything about that in your files.", calls=state["budget"].used,
        )}

    def after_gather(state: AskState) -> Literal["answer", "no_evidence"]:
        return "answer" if state["evidence"] else "no_evidence"

    def after_answer(state: AskState) -> Literal["gather", "check"]:
        return "gather" if state["draft"].status == "need_more" else "check"

    graph = StateGraph(AskState)
    for name, node in (("plan", plan), ("gather", gather), ("answer", answer), ("check", check),
                       ("no_evidence", no_evidence)):
        graph.add_node(name, node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "gather")
    graph.add_conditional_edges("gather", after_gather)
    graph.add_conditional_edges("answer", after_answer)
    graph.add_edge("check", END)
    graph.add_edge("no_evidence", END)
    return graph.compile()


def run_ask(graph, question: str, *, source_id: int | None = None, previous: str | None = None) -> AskResult:
    return graph.invoke({"question": question, "source_id": source_id, "previous": previous})["result"]


def _retrieve(
    tools: AskTools, specs: tuple[SearchSpec, ...], source_id: int | None, within: tuple[Path, ...] = (),
) -> list[tuple[SourceFragment, Source]]:
    if source_id is not None:
        source = tools.sources.get_by_id(source_id)
        if source is None:
            return []
        fragments = tools.fragments.list_for_source(source_id)
        if sum(len(fragment.text) for fragment in fragments) <= EVIDENCE_CHARACTERS:
            return [(fragment, source) for fragment in fragments]
    found: list[tuple[SourceFragment, Source]] = []
    for spec in specs:
        options: dict[str, object] = {"limit": PER_SEARCH}
        if source_id is not None:
            options["path_prefix"] = tools.sources.get_by_id(source_id).path  # type: ignore[union-attr]
        try:
            hits = tools.retriever.search(spec.query, **options)
        except (OSError, RuntimeError, ValueError):
            hits = ()
        found.extend((hit.fragment, hit.source) for hit in hits)  # type: ignore[attr-defined]
        for path in within:
            try:
                deeper = tools.retriever.search(spec.query, limit=PER_SEARCH, path_prefix=path)
            except (OSError, RuntimeError, ValueError):
                deeper = ()
            found.extend((hit.fragment, hit.source) for hit in deeper)  # type: ignore[attr-defined]
    return _with_neighbours(tools, found)


def _with_neighbours(
    tools: AskTools, found: list[tuple[SourceFragment, Source]],
) -> list[tuple[SourceFragment, Source]]:
    """Follow a very short match (a title slide) with the sections after it, where the content is.

    A bare heading with sections after it is replaced by them, so a fact can't be
    cited to the heading.
    """
    expanded: list[tuple[SourceFragment, Source]] = []
    sections: dict[int, list[SourceFragment]] = {}
    for fragment, source in found:
        following: list[SourceFragment] = []
        if len(fragment.text) < SHORT_SECTION and source.id is not None:
            ordered = sections.setdefault(source.id, list(tools.fragments.list_for_source(source.id)))
            position = next((index for index, item in enumerate(ordered) if item.id == fragment.id), None)
            if position is not None:
                following = ordered[position + 1:position + 1 + NEIGHBOURS]
        if len(fragment.text) >= TINY_SECTION or not following:
            expanded.append((fragment, source))
        expanded.extend((item, source) for item in following)
    return expanded


def _fit(evidence: list[Evidence]) -> list[Evidence]:
    kept, used = [], 0
    for item in evidence:
        if used + len(item.fragment.text) > EVIDENCE_CHARACTERS and kept:
            break
        kept.append(item)
        used += len(item.fragment.text)
    return kept


def _render(evidence: list[Evidence]) -> str:
    return "\n\n".join(
        f"[{item.key}] {item.source.path.name} · {item.fragment.location}\n{item.fragment.text[:EVIDENCE_CHARACTERS]}"
        for item in evidence
    )

