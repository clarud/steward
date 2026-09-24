"""Telegram cards for the three model flows: Find, Ask, and Summarize."""

from __future__ import annotations

from collections.abc import Callable

from steward.graphs.ask import AskResult, run_ask
from steward.graphs.find import FindResult, FindScope, run_find
from steward.graphs.summarize import SummaryResult, run_summarize
from steward.presentation import PresentedReply, ReplyAction
from steward.readable import label_citations, math_to_unicode, short_location
from steward.sources import Source

MAX_SOURCES_SHOWN = 5


class StewardAnswersApplication:
    """Runs a flow and turns its result into one card. Keeps each chat's last turn for follow-ups."""

    def __init__(
        self,
        *,
        find_graph: object,
        ask_graph: object,
        summarize_graph: object,
        location: Callable[[Source], str],
    ) -> None:
        self._find_graph = find_graph
        self._ask_graph = ask_graph
        self._summarize_graph = summarize_graph
        self._location = location
        self._last_find: dict[str, str] = {}
        self._last_ask: dict[str, str] = {}
        self._last_removed: dict[str, tuple[str, ...]] = {}

    def find(self, chat_id: str, request: str, scope: FindScope) -> PresentedReply:
        result = run_find(self._find_graph, request, previous=self._last_find.get(chat_id), scope=scope)
        self._last_find[chat_id] = f"{request} → " + (
            ", ".join(candidate.source.path.name for candidate, _ in result.picks) or result.kind
        )
        return self._find_card(request, result)

    def ask(self, chat_id: str, question: str, *, source: Source | None = None) -> PresentedReply:
        result = run_ask(
            self._ask_graph, question, source_id=source.id if source is not None else None,
            previous=self._last_ask.get(chat_id) if source is None else None,
        )
        if source is None:
            self._last_ask[chat_id] = f"Q: {question}\nA: {result.text[:300]}"
        labels, _ = _ask_labels(result)
        self._last_removed[chat_id] = tuple(_readable(line, labels) for line in result.removed_text)
        return self._ask_card(question, result, source)

    def removed(self, chat_id: str) -> PresentedReply | str:
        """What the checker removed from this chat's latest answer, so the owner can judge it."""
        removed = self._last_removed.get(chat_id)
        if not removed:
            return "Nothing was removed from your latest answer."
        return PresentedReply(
            "Removed because the cited sections didn't support them. If one looks right, open the source to check:\n\n"
            + "\n".join(f"• {line}" for line in removed),
            title="Removed statements", icon="🔍",
        )

    def summarize(self, source: Source) -> PresentedReply:
        return self._summary_card(source, run_summarize(self._summarize_graph, source.id or 0))

    # -- cards -----------------------------------------------------------------

    def _find_card(self, request: str, result: FindResult) -> PresentedReply:
        if result.kind in {"picks", "closest"} and result.picks:
            lines = ["No exact match. The closest files are:"] if result.kind == "closest" else []
            actions = []
            for index, (candidate, reason) in enumerate(result.picks, start=1):
                lines.append(f"{index}. 📄 {candidate.source.path.name}\n{self._location(candidate.source)}\n{math_to_unicode(reason)}")
                actions.append(ReplyAction(f"Open {index}", f"/source {candidate.source.id}"))
                actions.append(ReplyAction(f"Send {index}", f"/send_source {candidate.source.id}"))
            title = "Closest matches" if result.kind == "closest" else "Found"
            return PresentedReply("\n\n".join(lines), tuple(actions), title=title, icon="🔎")
        if result.kind == "clarify" and result.options:
            return PresentedReply(
                result.question,
                tuple(
                    ReplyAction(f"{index}. {candidate.source.path.name}"[:48], f"/source {candidate.source.id}")
                    for index, candidate in enumerate(result.options, start=1)
                ),
                title="Which one?", icon="🔎",
            )
        return PresentedReply(
            f"I couldn't find a file for that. I searched for {result.tried}.",
            (ReplyAction("💬 Ask instead", f"/ask {request}"), ReplyAction("Browse", "/sources")),
            title="No match", icon="🔎",
        )

    def _ask_card(self, question: str, result: AskResult, source: Source | None) -> PresentedReply:
        labels, shown = _ask_labels(result)
        lines = [_readable(result.text, labels)]
        if result.removed and result.status == "answered":
            lines.append(
                f"({result.removed} statement{'s' if result.removed != 1 else ''} removed: "
                "not supported by your files.)"
            )
        if shown and result.status == "answered":
            numbered = len(shown) > 1
            lines.append("Sources:\n" + "\n".join(
                (f"{index}. " if numbered else "• ") + self._location(item) + ": " + ", ".join(
                    dict.fromkeys(short_location(e.fragment.location) for e in result.cited if e.source.id == item.id)
                )
                for index, item in enumerate(shown, start=1)
            ))
        elif shown:
            lines.append("\n".join(f"• {self._location(item)}" for item in shown))
        actions = tuple(ReplyAction(f"Open {index}", f"/source {item.id}") for index, item in enumerate(shown, start=1))
        if result.removed_text:
            actions += (ReplyAction(f"Show removed ({len(result.removed_text)})", "/ask_removed"),)
        title = f"Answer: {source.path.name}" if source is not None else "Answer"
        return PresentedReply(
            "\n\n".join(lines), actions, title=title, icon="💬",
            reference=("source", source.id) if source is not None and source.id is not None else None,
        )

    def _summary_card(self, source: Source, result: SummaryResult) -> PresentedReply:
        read = ReplyAction("Read", f"/source_content {source.id}")
        ask = ReplyAction("Ask", f"/ask_source {source.id}")
        reference = ("source", source.id) if source.id is not None else None
        messages = {
            "no_text": "This file has no extracted text to summarise.",
            "unavailable": "The model couldn't summarise this right now. Try again shortly, or Read the file.",
            "too_long": f"This file is too long to summarise in one go ({result.total} sections). Read it, or Ask about one part.",
        }
        if result.status in messages:
            return PresentedReply(messages[result.status], (read, ask), title=source.path.name, icon="📄", reference=reference)
        lines = [_readable(result.text, {key: short_location(location) for key, location in result.cited})]
        if result.status == "notes_only":
            lines.insert(0, "I couldn't combine these into one summary, so here are the notes for each part:")
        coverage = f"Covered {result.covered} of {result.total} sections"
        if result.skipped:
            coverage += " · couldn't summarise " + "; ".join(result.skipped)
        lines.append(coverage + ".")
        return PresentedReply(
            "\n\n".join(lines), (read, ask), title=f"Summary: {source.path.name}", icon="📄", reference=reference,
        )


def _ask_labels(result: AskResult) -> tuple[dict[str, str], list[Source]]:
    """Citation labels for an answer: "p.3", or "2 p.3" when it cites several files (2 = its Sources number)."""
    files: dict[int, Source] = {}
    for item in result.cited:
        files.setdefault(item.source.id or 0, item.source)
    shown = list(files.values())[:MAX_SOURCES_SHOWN]
    number = {item.id: index for index, item in enumerate(shown, start=1)}
    labels = {}
    for item in result.cited:
        where = short_location(item.fragment.location)
        labels[item.key] = f"{number[item.source.id]} {where}" if len(shown) > 1 and item.source.id in number else where
    return labels, shown


def _readable(text: str, labels: dict[str, str]) -> str:
    return math_to_unicode(label_citations(text, labels))
