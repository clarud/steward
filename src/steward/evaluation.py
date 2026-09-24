"""Evaluation harnesses: finding files, the citation checker, Ask, and Summarize."""

from __future__ import annotations

import re
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class FileCase:
    """A request and the file a person expects.

    ``expected`` holds one or more acceptable path endings, for files that
    exist as identical copies or as a PDF and its transcript.
    """

    query: str
    expected: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FileEvaluation:
    case_count: int
    hit_at_1: float
    hit_at_3: float
    mean_reciprocal_rank: float
    misses: tuple[tuple[str, str], ...]


def load_file_cases(path: Path) -> tuple[FileCase, ...]:
    """Read `cases: [{query: ..., file: ...}]`; `files: [...]` lists several acceptable answers."""
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = document.get("cases") if isinstance(document, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("The case file needs a non-empty 'cases' list.")
    parsed = []
    for number, item in enumerate(cases, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Case {number} needs a query and the expected file.")
        expected = item.get("files", item.get("file"))
        if expected is None and isinstance(item.get("expected"), dict):
            expected = item["expected"].get("source")
        options = expected if isinstance(expected, list) else [expected]
        query = item.get("query")
        if not isinstance(query, str) or not query.strip() or not options or not all(
            isinstance(value, str) and value.strip() for value in options
        ):
            raise ValueError(f"Case {number} ({query!r}) needs a non-empty query and file.")
        parsed.append(FileCase(query.strip(), tuple(value.strip().replace("\\", "/") for value in options)))
    return tuple(parsed)


def evaluate_files(rank: Callable[[str], Sequence[Path]], cases: tuple[FileCase, ...]) -> FileEvaluation:
    """Score a ranking function that returns files best-first for a query."""
    if not cases:
        raise ValueError("At least one case is required.")
    reciprocal: list[float] = []
    hits_1 = hits_3 = 0
    misses = []
    for case in cases:
        endings = tuple(value.casefold() for value in case.expected)
        position = next(
            (index for index, path in enumerate(rank(case.query), start=1)
             if path.as_posix().casefold().endswith(endings)),
            None,
        )
        reciprocal.append(1 / position if position else 0.0)
        hits_1 += position == 1
        hits_3 += position is not None and position <= 3
        if position is None or position > 3:
            misses.append((case.query, " or ".join(case.expected)))
    count = len(cases)
    return FileEvaluation(count, hits_1 / count, hits_3 / count, sum(reciprocal) / count, tuple(misses))


# -- Checker: planted unsupported statements ---------------------------------


@dataclass(frozen=True, slots=True)
class Statement:
    text: str
    cite: str
    supported: bool


@dataclass(frozen=True, slots=True)
class CheckerCase:
    evidence: dict[str, str]
    statements: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class CheckerEvaluation:
    supported: int
    supported_kept: int
    unsupported: int
    unsupported_removed: int
    wrongly_removed: tuple[str, ...]
    missed: tuple[str, ...]


def load_checker_cases(path: Path) -> tuple[CheckerCase, ...]:
    """Read `cases: [{evidence: {F1: ...}, supported: [...], unsupported: [...]}]`.

    A statement is a string (cited as F1) or `{text, cite}`. Statements must not
    contain full stops, because the checker splits sentences on them.
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = document.get("cases") if isinstance(document, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("The case file needs a non-empty 'cases' list.")
    parsed = []
    for number, item in enumerate(cases, start=1):
        evidence = item.get("evidence") if isinstance(item, dict) else None
        if isinstance(evidence, str):
            evidence = {"F1": evidence}
        if not isinstance(evidence, dict) or not evidence:
            raise ValueError(f"Case {number} needs evidence.")
        statements = []
        for supported, key in ((True, "supported"), (False, "unsupported")):
            for entry in item.get(key) or []:
                text, cite = (entry, "F1") if isinstance(entry, str) else (entry.get("text"), entry.get("cite", "F1"))
                if not isinstance(text, str) or not text.strip() or cite not in evidence:
                    raise ValueError(f"Case {number} has a statement without text or with an unknown cite.")
                if any(mark in text.strip().rstrip(".!?") for mark in ".!?\n"):
                    raise ValueError(f"Case {number}: keep each statement to one sentence with no inner full stop.")
                statements.append(Statement(text.strip().rstrip(".!?"), cite, supported))
        parsed.append(CheckerCase({str(k): str(v) for k, v in evidence.items()}, tuple(statements)))
    return tuple(parsed)


def evaluate_checker(check: Callable[[str, dict[str, str]], str], cases: tuple[CheckerCase, ...]) -> CheckerEvaluation:
    """Run ``check(answer, evidence) -> kept text`` on answers mixing true and planted statements."""
    supported = kept = unsupported = removed = 0
    wrongly_removed, missed = [], []
    for case in cases:
        # A fixed shuffle, so position gives nothing away and reruns are comparable.
        ordered = sorted(case.statements, key=lambda item: zlib.crc32(item.text.encode("utf-8")))
        answer = "\n".join(f"{item.text} [{item.cite}]." for item in ordered)
        result = check(answer, case.evidence)
        for item in case.statements:
            survived = f"{item.text} [{item.cite}]" in result
            if item.supported:
                supported += 1
                kept += survived
                if not survived:
                    wrongly_removed.append(item.text)
            else:
                unsupported += 1
                removed += not survived
                if survived:
                    missed.append(item.text)
    return CheckerEvaluation(supported, kept, unsupported, removed, tuple(wrongly_removed), tuple(missed))


# -- Ask and Summarize: run the flow, score what code can score, report the rest --


@dataclass(frozen=True, slots=True)
class AskCase:
    question: str
    expected: tuple[str, ...]  # empty: the files don't answer it, so Steward should say so


def load_ask_cases(path: Path) -> tuple[AskCase, ...]:
    """Read `cases: [{question: ..., files: [...]}]`; `answerable: false` for questions the files can't answer."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = document.get("cases") if isinstance(document, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("The case file needs a non-empty 'cases' list.")
    parsed = []
    for number, item in enumerate(cases, start=1):
        question = item.get("question") if isinstance(item, dict) else None
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"Case {number} needs a question.")
        if item.get("answerable") is False:
            parsed.append(AskCase(question.strip(), ()))
            continue
        expected = item.get("files", item.get("file"))
        options = expected if isinstance(expected, list) else [expected]
        if not all(isinstance(value, str) and value.strip() for value in options):
            raise ValueError(f"Case {number} needs the file(s) that answer it, or answerable: false.")
        parsed.append(AskCase(question.strip(), tuple(value.strip().replace("\\", "/") for value in options)))
    return tuple(parsed)


def cites_expected(cited: Sequence[Path], expected: tuple[str, ...]) -> bool:
    endings = tuple(value.casefold() for value in expected)
    return any(path.as_posix().casefold().endswith(endings) for path in cited)


def load_summary_cases(path: Path) -> tuple[str, ...]:
    """Read `cases: [{file: ...}]`: the files to summarise, by the end of their path."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = document.get("cases") if isinstance(document, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("The case file needs a non-empty 'cases' list.")
    files = []
    for number, item in enumerate(cases, start=1):
        value = item.get("file") if isinstance(item, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Case {number} needs a file.")
        files.append(value.strip().replace("\\", "/"))
    return tuple(files)


_DECLINE = re.compile(
    r"\b(?:couldn['’]?t|could not|can['’]?t|cannot|don['’]?t|doesn['’]?t|does not|isn['’]?t|"
    r"no (?:information|mention|evidence)|not (?:in|covered|mentioned|found))\b",
    re.IGNORECASE,
)


def declines(status: str, text: str, cited: Sequence[Path]) -> bool:
    """Did Ask decline? Only a cited answer counts as answering; an uncited reply must also say it can't answer."""
    if status != "answered":
        return True
    return not cited and bool(_DECLINE.search(text))
