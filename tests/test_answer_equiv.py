"""Tests for robust answer equivalence — the exact cases the diagnosis surfaced."""
from __future__ import annotations

from prax.eval.answer_equiv import answers_equivalent as eq


def test_fraction_decimal_equivalence():
    # The diagnosed scoring artifacts — all should be equivalent now.
    assert eq("0.25", r"\frac{1}{4}")
    assert eq("5.5", r"\frac{11}{2}")
    assert eq(r"\dfrac{1}{4}", "0.25")
    assert eq("1/4", "0.25")
    assert eq("11/2", "5.5")


def test_spacing_and_format():
    assert eq("6 + 9i", "6+9i")
    assert eq("$32$", "32")
    assert eq("32.", "32")
    assert eq(r"32\!", "32")


def test_exact_rational_not_float_noise():
    assert eq("1/3", "0.3333333333333333")   # within tolerance
    assert not eq("1/3", "0.3")              # genuinely different


def test_real_inequality_still_fails():
    assert not eq("5", "6")
    assert not eq(r"\frac{1}{4}", r"\frac{1}{3}")
    assert not eq("apple", "banana")
    assert not eq(None, "5")


def test_returns_bool_never_raises():
    for a, b in (("", ""), ("1/0", "5"), ("\\sqrt{", "2"), ("x+", "y")):
        assert isinstance(eq(a, b), bool)


def test_degrees_and_units():
    assert eq(r"76^\circ", "76")
    assert eq("90°", "90")
    assert not eq(r"76^\circ", "77")


def test_solution_set_order_insensitive():
    assert eq("-2,1", "1,-2")
    assert eq("1, 2, 3", "3, 1, 2")
    assert not eq("-2,1", "1,-3")


def test_thousands_not_treated_as_set():
    assert eq("1,000", "1000")
    assert eq("12,345", "12345")
    assert not eq("1,000", "1,001")


def test_boxed_nested_braces_extraction():
    from prax.eval.benchmarks.math_bench import _extract_answer
    assert _extract_answer(r"thus \boxed{\frac{1}{4}}.") == r"\frac{1}{4}"
    assert _extract_answer(r"\boxed{\begin{pmatrix}1&0\\0&1\end{pmatrix}}") == \
        r"\begin{pmatrix}1&0\\0&1\end{pmatrix}"
    assert _extract_answer("no box here, answer is 42") == "42"
