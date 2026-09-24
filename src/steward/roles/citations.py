"""Citation keys such as [F12], in one canonical form.

Models don't always copy the form they're shown: they write [F1, F2],
[F1; F2], or [Fn: F12]. Every model reply is normalised to [F1][F2] before it
is validated, so the checker and coverage see every citation.
"""

from __future__ import annotations

import re

KEY = re.compile(r"\[(F\d+)\]")
_GROUP = re.compile(r"\[([^\[\]\n]{1,160})\]")
_ID = re.compile(r"(?<![A-Za-z0-9])F\d+(?![0-9])")


def normalize(text: str) -> str:
    """Rewrite any bracket group that names section keys as one [Fn] per key."""

    def rewrite(match: re.Match[str]) -> str:
        ids = _ID.findall(match.group(1))
        return "".join(f"[{key}]" for key in dict.fromkeys(ids)) if ids else match.group(0)

    return _GROUP.sub(rewrite, text)


def keys(text: str) -> list[str]:
    """Section keys cited in normalised text, in order of first appearance."""
    return list(dict.fromkeys(KEY.findall(text)))
