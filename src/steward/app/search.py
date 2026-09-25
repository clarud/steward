"""Parse `/find words [--type TYPE] [--root "NAME"]` into a request and explicit filters.

Phone keyboards rewrite what people type: "--" becomes an em or en dash and
straight quotes become curly ones. Both are accepted, as is `root:NAME` /
`type:pdf`, so a root the owner asked for is never silently ignored.
"""

from __future__ import annotations

import shlex

from steward.graphs.find import FindScope
from steward.roots import SourceRootRepository
from steward.sources import SourceType


def parse_find(raw: str, roots: SourceRootRepository) -> tuple[str, FindScope] | str:
    """Return (request, scope), or a message explaining what to fix. Roots resolve only by name."""
    try:
        tokens = shlex.split(raw.translate(_CURLY_QUOTES))
    except ValueError:
        return "Your search has an unmatched quote. Close it and try again."
    terms: list[str] = []
    types: list[SourceType] = []
    root_name: str | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        flag, inline = _flag(token)
        if flag is not None and inline:
            tokens[index:index + 1] = [flag, inline]
            token = flag
        elif flag is not None:
            token = flag
        if token in {"--type", "--root"}:
            index += 1
            if index == len(tokens):
                return f"Add a value after {token}, for example --type pdf or --root \"Y4S1\"."
            if token == "--type":
                try:
                    types.append(SourceType(tokens[index].casefold()))
                except ValueError:
                    return "Unknown type. Try markdown, plain_text, pdf, docx, pptx, xlsx, notebook, html, image, or code."
            else:
                root_name = tokens[index]
        else:
            terms.append(token)
        index += 1
    if not terms:
        return "Add some words to look for, for example /find queueing notes --type pdf."
    if root_name is not None:
        root = next((item for item in roots.list_all() if item.name.casefold() == root_name.casefold()), None)
        if root is None:
            return f"No folder is named {root_name!r}. Open /sources to see your folders."
        root_name = root.name
    return " ".join(terms), FindScope(tuple(dict.fromkeys(types)), root_name)


def find_command(request: str, scope: FindScope) -> str:
    """The `/find` command that repeats a search with this scope (for buttons)."""
    parts = ["/find", request]
    parts += [f"--type {item.value}" for item in scope.types]
    if scope.root:
        parts.append(f'--root "{scope.root}"')
    return " ".join(parts)


_CURLY_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'"})
_DASHES = ("--", "—", "–", "−")


def _flag(token: str) -> tuple[str | None, str]:
    """("--root", "") for "--root" or "—root"; ("--root", "Y4S1") for "root:Y4S1"."""
    lowered = token.casefold()
    for name in ("root", "type"):
        if any(lowered == dash + name for dash in _DASHES):
            return f"--{name}", ""
        if lowered.startswith(name + ":") and len(token) > len(name) + 1:
            return f"--{name}", token[len(name) + 1:]
    return None, ""
