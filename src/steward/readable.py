"""Turn model and file text into something readable in a chat.

Telegram shows text as written: LaTeX such as ``$1/(\\mu - \\lambda)$`` stays raw,
and section keys such as ``[F4058]`` mean nothing to a reader. ``math_to_unicode``
rewrites inline maths with Unicode (λ, ≤, ρ², Tᵢ), and ``label_citations`` swaps
keys for locations such as ``[p.19]``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_COMMANDS = {
    # Greek
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ",
    "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    # relations and operators
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠", "approx": "≈", "sim": "∼",
    "equiv": "≡", "triangleq": "≜", "propto": "∝", "times": "×", "cdot": "·", "pm": "±", "mp": "∓",
    "div": "÷", "infty": "∞", "sum": "Σ", "prod": "∏", "int": "∫", "partial": "∂", "nabla": "∇",
    "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "cup": "∪", "cap": "∩", "forall": "∀",
    "exists": "∃", "to": "→", "rightarrow": "→", "Rightarrow": "⇒", "implies": "⇒", "leftarrow": "←",
    "Leftarrow": "⇐", "leftrightarrow": "↔", "iff": "⇔", "mid": "|", "lfloor": "⌊", "rfloor": "⌋",
    "lceil": "⌈", "rceil": "⌉", "cdots": "⋯", "ldots": "…", "dots": "…",
    # names and spacing
    "log": "log", "ln": "ln", "exp": "exp", "min": "min", "max": "max", "lim": "lim", "sin": "sin",
    "cos": "cos", "quad": " ", "qquad": "  ", "left": "", "right": "", "big": "", "Big": "", "bigl": "",
    "bigr": "", "displaystyle": "",
}
_ACCENTS = {"tilde": "\u0303", "hat": "\u0302", "bar": "\u0304", "vec": "\u20d7", "dot": "\u0307"}
_SUPERSCRIPT = dict(zip("0123456789+-=()nijabcdefghklmoprstuvwxyzT", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱʲᵃᵇᶜᵈᵉᶠᵍʰᵏˡᵐᵒᵖʳˢᵗᵘᵛʷˣʸᶻᵀ"))
_SUBSCRIPT = dict(zip("0123456789+-=()aehijklmnoprstuvx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ"))

_MATH = re.compile(r"\$\$(.+?)\$\$|\$([^$\n]{1,300}?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.DOTALL)
# A "$...$" pair is maths if it holds LaTeX-ish characters, or is a lone symbol or
# function such as $S$, $T_3$, or $E(W)$; "$5 and $10" is money and stays as it is.
_LOOKS_LIKE_MATH = re.compile(r"[\\^_{}=<>]|^\s*[A-Za-z][A-Za-z0-9]*(?:_?\w)?(?:\([^()$]*\))?\s*$")
# Fractions are built between these markers, and keep their parentheses only when
# something follows directly, as in (d/dx)F(x).
_OPEN, _CLOSE = "\ue000", "\ue001"
_FRAC = re.compile(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}")
_SQRT = re.compile(r"\\sqrt\{([^{}]*)\}")
_WRAPPER = re.compile(r"\\(?:text|mathrm|mathbf|mathit|mathsf|mathtt|mathcal|mathbb|operatorname|boldsymbol)\{([^{}]*)\}")
_ACCENT = re.compile(r"\\(" + "|".join(_ACCENTS) + r")\{([^{}]*)\}")
_COMMAND = re.compile(r"\\([A-Za-z]+)")
_BRACED_SCRIPT = re.compile(r"([_^])\{([^{}]*)\}")
_PLAIN_SCRIPT = re.compile(r"([_^])([A-Za-z0-9+\-])")


def math_to_unicode(text: str) -> str:
    """Rewrite inline LaTeX maths as Unicode; other text (including "$5 and $10") is untouched."""

    def convert(match: re.Match[str]) -> str:
        inner = next(group for group in match.groups() if group is not None)
        if match.group(2) is not None and not _LOOKS_LIKE_MATH.search(inner):
            return match.group(0)  # dollar amounts, not maths
        return _latex(inner)

    return _MATH.sub(convert, text)


def _latex(math: str) -> str:
    text = math
    for _ in range(4):  # innermost first; nested fractions resolve over a few passes
        previous = text
        text = _WRAPPER.sub(r"\1", text)
        text = _ACCENT.sub(lambda m: m.group(2) + _ACCENTS[m.group(1)], text)
        text = _SQRT.sub(lambda m: "√" + _wrap(m.group(1)), text)
        text = _FRAC.sub(lambda m: f"{_OPEN}{_wrap(m.group(1))}/{_wrap(m.group(2))}{_CLOSE}", text)
        if text == previous:
            break
    text = text.replace(r"\{", "{").replace(r"\}", "}").replace(r"\|", "‖").replace(r"\%", "%")
    text = re.sub(r"\\[,;:! ]", " ", text)
    text = _COMMAND.sub(lambda m: _COMMANDS.get(m.group(1), m.group(1)), text)
    for _ in range(4):
        previous = text
        text = _BRACED_SCRIPT.sub(lambda m: _script(m.group(1), m.group(2)), text)
        if text == previous:
            break
    text = _PLAIN_SCRIPT.sub(lambda m: _script(m.group(1), m.group(2)), text)
    text = text.replace("{", "").replace("}", "")
    text = re.sub(f"{_OPEN}([^{_OPEN}{_CLOSE}]*){_CLOSE}(?=[\\w(])", r"(\1)", text)
    text = text.replace(_OPEN, "").replace(_CLOSE, "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _script(mark: str, content: str) -> str:
    content = _PLAIN_SCRIPT.sub(lambda m: _script(m.group(1), m.group(2)), content)
    table = _SUPERSCRIPT if mark == "^" else _SUBSCRIPT
    if content and all(char in table for char in content):
        return "".join(table[char] for char in content)
    return mark + (content if re.fullmatch(r"[A-Za-z0-9]+", content) else f"({content})")


def _wrap(part: str) -> str:
    part = part.strip()
    return f"({part})" if re.search(r"[\s+\-*/]", part) else part


# -- citations -----------------------------------------------------------------

_RUN = re.compile(r"\[F\d+\](?:\s*[,;]?\s*\[F\d+\])*")
_KEY = re.compile(r"F\d+")


def short_location(location: str) -> str:
    """"page 19" → "p.19"; other locations stay readable but short."""
    location = location.strip()
    match = re.fullmatch(r"pages? (\d+)(?:\s*[-–]\s*(\d+))?(?: OCR)?", location)
    if match:
        return f"p.{match.group(1)}" + (f"–{match.group(2)}" if match.group(2) else "")
    match = re.fullmatch(r"lines (\d+)-(\d+)", location)
    if match:
        return f"lines {match.group(1)}–{match.group(2)}"
    return location if len(location) <= 24 else location[:23] + "…"


def label_citations(text: str, labels: Mapping[str, str]) -> str:
    """Replace each run of keys such as "[F12][F13]" with "[p.3, p.4]"; unknown keys are dropped."""

    def replace(match: re.Match[str]) -> str:
        names = list(dict.fromkeys(labels[key] for key in _KEY.findall(match.group(0)) if key in labels))
        return f"[{', '.join(names)}]" if names else ""

    return re.sub(r"[ \t]+([.,;:])", r"\1", _RUN.sub(replace, text))
