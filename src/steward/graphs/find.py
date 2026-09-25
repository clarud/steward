"""Find: plan → four retrievers in parallel → merge per file → judge (→ reformulate once)."""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from steward.answer.gateway import ModelGateway
from steward.extraction import InvalidSearchQueryError
from steward.observability import trace
from steward.retrieval import LexicalSearchService, SemanticSearchService
from steward.retrieval.files import FileCandidate, files_only, fuse, group_by_file
from steward.roles.find import FindPlan, JudgeDecision, Pick, judge_candidates, plan_find
from steward.roles.structured import CallBudget, StructuredOutputError
from steward.roots import SourceRootRepository
from steward.sources import Source, SourceRepository, SourceType

FIND_BUDGET = 4
PER_RETRIEVER = 10
JUDGED = 8
RETRIEVERS = ("keyword", "meaning", "filename", "recent")


@dataclass(frozen=True, slots=True)
class FindScope:
    """Filters the owner typed explicitly; they override the Planner."""

    types: tuple[SourceType, ...] = ()
    root: str | None = None


@dataclass(frozen=True, slots=True)
class FindResult:
    kind: str  # picks | clarify | closest | no_match
    picks: tuple[tuple[FileCandidate, str], ...] = ()
    question: str = ""
    options: tuple[FileCandidate, ...] = ()
    tried: str = ""
    calls: int = 0


@dataclass
class FindTools:
    sources: SourceRepository
    roots: SourceRootRepository
    lexical: LexicalSearchService
    semantic: SemanticSearchService | None
    model: ModelGateway | None
    location: Callable[[Source], str]
    today: Callable[[], date] = date.today


class FindState(TypedDict, total=False):
    request: str
    previous: str | None
    scope: FindScope
    budget: CallBudget
    plan: FindPlan
    attempt: int
    candidates: Annotated[dict[str, list[FileCandidate]], operator.or_]
    merged: list[FileCandidate]
    decision: JudgeDecision
    result: FindResult


def build_find_graph(tools: FindTools):
    def plan(state: FindState) -> dict:
        budget = state.get("budget") or CallBudget(FIND_BUDGET if tools.model is not None else 0)
        chosen = FindPlan.literal(state["request"])
        if tools.model is not None:
            try:
                chosen = plan_find(
                    tools.model, budget, request=state["request"], today=tools.today(),
                    roots=_root_folders(tools), previous=state.get("previous"),
                )
            except StructuredOutputError:
                trace("find.plan_fallback")
        trace("find.plan", calls=budget.used)
        return {"plan": chosen, "budget": budget, "attempt": state.get("attempt", 0),
                "scope": state.get("scope") or FindScope()}

    def keyword(state: FindState) -> dict:
        query = " OR ".join('"' + word.replace('"', '""') + '"' for word in state["plan"].keywords)
        try:
            hits = tools.lexical.search(query, **_filters(tools, state["scope"], PER_RETRIEVER))
        except (InvalidSearchQueryError, ValueError):
            hits = ()
        return {"candidates": {"keyword": group_by_file(hits, "keyword")}}

    def meaning(state: FindState) -> dict:
        if tools.semantic is None:
            return {"candidates": {"meaning": []}}
        try:
            hits = tools.semantic.search(state["plan"].meaning_query, **_filters(tools, state["scope"], PER_RETRIEVER))
        except (OSError, RuntimeError, ValueError):
            hits = ()
        return {"candidates": {"meaning": group_by_file(hits, "meaning")}}

    def filename(state: FindState) -> dict:
        found_plan, scope = state["plan"], state["scope"]
        terms = [found_plan.filename_hint, found_plan.folder_hint, " ".join(found_plan.keywords)]
        found: list[Source] = []
        for term in (item for item in terms if item):
            found.extend(tools.sources.search_filenames(
                term, limit=PER_RETRIEVER, source_types=scope.types or None, path_prefix=_root_path(tools, scope.root),
            ))
        return {"candidates": {"filename": files_only(found, "filename")[:PER_RETRIEVER]}}

    def recent(state: FindState) -> dict:
        found_plan, scope = state["plan"], state["scope"]
        if found_plan.since is None:
            return {"candidates": {"recent": []}}
        cutoff = datetime.combine(found_plan.since, datetime.min.time()).astimezone()
        root_path = _root_path(tools, scope.root)
        newest = sorted(
            (
                source for source in tools.sources.list_active()
                if max(source.modified_at, source.first_seen_at) >= cutoff
                and (not scope.types or source.source_type in scope.types)
                and (root_path is None or source.path.is_relative_to(root_path))
            ),
            key=lambda source: max(source.modified_at, source.first_seen_at), reverse=True,
        )
        return {"candidates": {"recent": files_only(newest, "recent")[:PER_RETRIEVER]}}

    def merge(state: FindState) -> dict:
        merged = fuse(state.get("candidates", {}), limit=JUDGED, boost=_preferences(tools, state["plan"]))
        trace("find.merge", candidates=len(merged), attempt=state.get("attempt", 0))
        return {"merged": merged}

    def judge(state: FindState) -> dict:
        merged = state["merged"]
        budget = state["budget"]
        if tools.model is None or budget.remaining <= 0:
            return {"decision": _fallback_decision(merged)}
        try:
            decision = judge_candidates(
                tools.model, budget, request=state["request"],
                candidates=[_describe(tools, candidate) for candidate in merged],
            )
        except StructuredOutputError:
            trace("find.judge_fallback")
            decision = _fallback_decision(merged)
        trace("find.judge", decision=decision.kind, calls=budget.used)
        return {"decision": decision}

    def reformulate(state: FindState) -> dict:
        budget = state["budget"]
        broader = FindPlan.literal(state["request"])
        try:
            broader = plan_find(
                tools.model, budget, request=state["request"], today=tools.today(),  # type: ignore[arg-type]
                roots=_root_folders(tools), failed_plan=state["plan"],
            )
        except StructuredOutputError:
            trace("find.reformulate_fallback")
        trace("find.reformulate", calls=budget.used)
        # Each retriever overwrites its own entry in `candidates` on the second pass.
        return {"plan": broader, "attempt": 1}

    def respond(state: FindState) -> dict:
        decision = state.get("decision") or JudgeDecision("no_match")
        if decision.kind == "no_match" and state.get("merged"):
            # The judge rejected every candidate even after a broader search: show the
            # closest files, labelled as such, rather than nothing.
            decision = replace(_fallback_decision(state["merged"]), kind="closest")
        merged = {candidate.source.id: candidate for candidate in state.get("merged", [])}
        result = FindResult(
            kind=decision.kind,
            picks=tuple((merged[pick.source_id], pick.reason) for pick in decision.picks if pick.source_id in merged),
            question=decision.question,
            options=tuple(merged[item] for item in decision.options if item in merged),
            tried=_describe_search(state["plan"], state["scope"]),
            calls=state["budget"].used,
        )
        return {"result": result}

    def after_merge(state: FindState) -> Literal["judge", "reformulate", "respond"]:
        if state["merged"]:
            return "judge"
        return "reformulate" if _can_reformulate(tools, state) else "respond"

    def after_judge(state: FindState) -> Literal["reformulate", "respond"]:
        if state["decision"].kind == "no_match" and _can_reformulate(tools, state):
            return "reformulate"
        return "respond"

    graph = StateGraph(FindState)
    for name, node in (("plan", plan), ("keyword", keyword), ("meaning", meaning), ("filename", filename),
                       ("recent", recent), ("merge", merge), ("judge", judge), ("reformulate", reformulate),
                       ("respond", respond)):
        graph.add_node(name, node)
    graph.add_edge(START, "plan")
    for retriever in RETRIEVERS:
        graph.add_edge("plan", retriever)
        graph.add_edge("reformulate", retriever)
    graph.add_edge(list(RETRIEVERS), "merge")
    graph.add_conditional_edges("merge", after_merge)
    graph.add_conditional_edges("judge", after_judge)
    graph.add_edge("respond", END)
    return graph.compile()


def run_find(graph, request: str, *, previous: str | None = None, scope: FindScope | None = None) -> FindResult:
    state = graph.invoke({"request": request, "previous": previous, "scope": scope or FindScope()})
    return state["result"]


def _can_reformulate(tools: FindTools, state: FindState) -> bool:
    # Reformulating needs one planner call and leaves one for the judge.
    return tools.model is not None and state.get("attempt", 0) == 0 and state["budget"].remaining >= 2


def _fallback_decision(merged: list[FileCandidate]) -> JudgeDecision:
    if not merged:
        return JudgeDecision("no_match")
    return JudgeDecision("picks", tuple(Pick(candidate.source.id or 0, _plain_reason(candidate)) for candidate in merged[:3]))


def _plain_reason(candidate: FileCandidate) -> str:
    if candidate.fragment is not None:
        return f"matched text at {candidate.fragment.location}"
    if "recent" in candidate.found_by:
        return "added or changed recently"
    return "file or folder name matched"


def _describe(tools: FindTools, candidate: FileCandidate) -> dict[str, object]:
    snippet = " ".join(candidate.fragment.text.split())[:300] if candidate.fragment is not None else ""
    return {
        "id": candidate.source.id,
        "location": tools.location(candidate.source),
        "type": candidate.source.source_type.value,
        "modified": candidate.source.modified_at.date().isoformat(),
        "found_by": ", ".join(sorted(candidate.found_by)),
        "snippet": snippet,
    }


def _preferences(tools: FindTools, plan: FindPlan) -> Callable[[Source], float] | None:
    """The planner's root, types, and folder only nudge the ranking; they never exclude a file."""
    root_path = _root_path(tools, plan.root)
    folder = (plan.folder_hint or "").casefold()
    if root_path is None and not plan.types and not folder:
        return None

    def boost(source: Source) -> float:
        score = 0.0
        if root_path is not None and source.path.is_relative_to(root_path):
            score += 0.005
        if plan.types and source.source_type in plan.types:
            score += 0.005
        if folder and folder in str(source.path).casefold():
            score += 0.01
        return score

    return boost


def _describe_search(plan: FindPlan, scope: FindScope) -> str:
    description = plan.describe()
    if scope.root:
        description += f"; only in {scope.root}"
    if scope.types:
        description += "; only " + ", ".join(item.value for item in scope.types)
    return description


def _root_path(tools: FindTools, root_name: str | None) -> Path | None:
    if root_name is None:
        return None
    root = tools.roots.get_by_name(root_name)
    return root.path if root is not None else None


def _filters(tools: FindTools, scope: FindScope, limit: int) -> dict[str, object]:
    """Only the owner's explicit --type and --root are hard filters."""
    options: dict[str, object] = {"limit": limit}
    if scope.types:
        options["source_types"] = scope.types
    root_path = _root_path(tools, scope.root)
    if root_path is not None:
        options["path_prefix"] = root_path
    return options


def _root_folders(tools: FindTools) -> dict[str, list[str]]:
    folders: dict[str, list[str]] = {}
    for root in tools.roots.list_all():
        try:
            folders[root.name] = sorted(
                child.name for child in root.path.iterdir() if child.is_dir() and not child.name.startswith(".")
            )
        except OSError:
            folders[root.name] = []
    return folders
