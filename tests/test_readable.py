"""Readable text for chat: LaTeX to Unicode, section keys to locations."""

from steward.readable import label_citations, math_to_unicode, short_location


def test_common_latex_becomes_unicode() -> None:
    assert math_to_unicode(r"$E(W) = 1/(\mu - \lambda)$") == "E(W) = 1/(μ - λ)"
    assert math_to_unicode(r"$\pi_i = \rho^i (1-\rho)$") == "πᵢ = ρⁱ (1-ρ)"
    assert math_to_unicode(r"$\lambda = \lambda_1 + \lambda_2$") == "λ = λ₁ + λ₂"
    assert math_to_unicode(r"$E(Q) = \rho^2/(1-\rho)$") == "E(Q) = ρ²/(1-ρ)"
    assert math_to_unicode(r"$\sum_{j=1}^n \lambda_j P_{ji}$") == "Σⱼ₌₁ⁿ λⱼ Pⱼᵢ"
    assert math_to_unicode(r"$\lambda = r(I - P)^{-1}$") == "λ = r(I - P)⁻¹"
    assert math_to_unicode(r"$f(x) = \frac{d}{dx}F(x)$") == "f(x) = (d/dx)F(x)"
    assert math_to_unicode(r"$\tilde{\rho}$ and $T_{A2}$") == "ρ̃ and T_A2"
    assert math_to_unicode(r"display: $$0 \le P(E) \le 1$$") == "display: 0 ≤ P(E) ≤ 1"


def test_found_rendering_real_summaries() -> None:
    # Simple function notation is maths, not money.
    assert math_to_unicode("the average $E(W)$ and $P(E)$ and $T_3$") == "the average E(W) and P(E) and T₃"
    # Parentheses from the source are kept: (λ/k)/(μ/k) is not λ/k/μ/k.
    assert math_to_unicode(r"$\rho = (\lambda/k)/(\mu/k)$") == "ρ = (λ/k)/(μ/k)"
    assert math_to_unicode(r"$\frac{\lambda}{\mu}$ and $\frac{1}{1-\rho}$") == "λ/μ and 1/(1-ρ)"
    # A subscript inside a superscript stays readable.
    assert math_to_unicode(r"$\rho_i^{l_i}$") == "ρᵢ^(lᵢ)"


def test_prose_and_dollar_amounts_are_left_alone() -> None:
    assert math_to_unicode("It costs $5 and $10 today.") == "It costs $5 and $10 today."
    assert math_to_unicode("No maths here.") == "No maths here."


def test_citation_keys_become_locations() -> None:
    labels = {"F1": "p.36", "F2": "p.36", "F3": "slide 2"}

    assert label_citations("Speedup is 1/f [F1][F2]. Also [F2], [F99] and [F3].", labels) == (
        "Speedup is 1/f [p.36]. Also [p.36] and [slide 2]."
    )
    assert label_citations("Only an unknown key [F99].", labels) == "Only an unknown key."
    assert [short_location(item) for item in ("page 19", "page 3 OCR", "lines 12-40", "slide 7")] == [
        "p.19", "p.3", "lines 12–40", "slide 7",
    ]
