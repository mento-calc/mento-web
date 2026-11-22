"""Continuous beams of Karnovsky and Lebed, solved by ``py/analysis.py``.

Source: Igor A. Karnovsky and Olga Lebed, *Advanced Methods of Structural Analysis: Strength,
Stability, Vibration*, 2nd edition, Springer Nature Switzerland, 2021 (ISBN 978-3-030-44393-1):

- §13.6.1 *Fixed Loads* (pp. 471-473), the same beam as §10.2.4.1 (pp. 374-376): two spans
  under a uniform load and a point load.
- §13.6.3 *Moving Load*, Example 13.5 (pp. 476-480): the influence lines of the moments at the
  two inner supports of three equal spans, read at six positions of a unit load.
- §9.3.1 (Fig. 9.8, pp. 282-283): two equal spans, a point load at 0.4·l of the second.
- §9.3.2, Examples 9.2 (pp. 287-289) and 9.3 (p. 290): three spans under a point load and a
  uniform load; four equal spans with the first loaded.

Transcribed from mako's validation net (``tests/validation/fem/test_continuous_beams_karnovsky.py``),
which read each value off the book and documented the slips it found. Left out here, because the
solver does not take what they load: Example 13.4 (settlements and a turned clamp), Example 9.6
(an internal hinge) and Example 9.11 (a temperature gradient).

**Transcription.** The book draws a beam with x to the right and y up, its moments on the
stretched fibre and its shear ``Q = dM/dx``: the solver's signs, sagging positive. A reaction
upwards is positive in both. The book's joint rotation ``Z`` is positive **clockwise**, the
solver's ``dv/dx`` counterclockwise: ``Z = −θ``. Units: kN and m, as the book.
"""

from __future__ import annotations

import pytest
from analysis import Beam, Point, Uniform

from . import _cases
from ._cases import ValidationCase

BOOK = "Karnovsky & Lebed, Advanced Methods of Structural Analysis, 2nd ed. (2021)"
#: kN·m²: no printed value depends on it.
EI = 1.0e4


def _spans(lengths: list[float]) -> Beam:
    return Beam(tuple(lengths), (EI,) * len(lengths), ("pin",) * (len(lengths) + 1))


def _two_spans_13_6_1() -> list[float]:
    """§13.6.1 (pp. 471-473) and §10.2.4.1 (pp. 374-376): two spans, q and P.

    Span A-1 of l₁ = 8 m under q = 2 kN/m; span 1-B of l₂ = 10 m with P = 12 kN at 6 m from
    support 1. The rotation of joint 1, by the displacement method: Z₁ = 4.16·40/(27·EI) =
    **6.163/EI** clockwise (p. 375; p. 472 by the matrix method). The moment over support 1,
    **−18.31 kN·m** (p. 375); under the load, **21.475 kN·m** (p. 376).

    Read here: Z₁·EI (kN·m²), M over support 1 and under P (kN·m).
    """
    solution = _cases.solve(_spans([8.0, 10.0]), {"q + P": [Uniform(0, 2.0), Point(1, 12.0, 6.0)]})
    result = solution["q + P"]
    return [-result.rotation(8.0) * EI, result.moment(8.0), result.moment(14.0)]


#: Example 13.5: the six positions of the unit load, by the book's section number — span and
#: distance from the span's left support, as a fraction of l.
_SECTIONS = {2: (0, 1 / 3), 4: (0, 2 / 3), 8: (1, 1 / 3), 10: (1, 2 / 3), 14: (2, 1 / 3), 16: (2, 2 / 3)}
#: Fig. 13.25d (p. 477), factor 0.01·l: (IL(M_B), IL(M_C)) at sections 2, 4, 8, 10, 14, 16.
_ORDINATES = ((-7.893, 1.973), (-9.872, 2.468), (-7.897, -5.426), (-5.426, -7.897), (2.468, -9.872), (1.973, -7.893))


def _influence_lines_13_5() -> list[float]:
    """Example 13.5 (§13.6.3, pp. 476-480): influence lines of M_B and M_C.

    Three equal spans l, EI constant; a unit load moves along the beam. K = EI/l·[[7, 2], [2, 7]]
    (p. 478); the ordinates of Fig. 13.25d (p. 477) and Table 13.1 (p. 479), factor 0.01·l.
    The book rounds u to 0.333 and 0.667; the exact sections move the ordinates by up to 0.12 %
    (section 2: 4/15·8/27 = 0.07901·l against 0.07893·l), hence the tolerance.

    Read here: M at B and at C for each position, over P·l and times 100.
    """
    span = 6.0
    loads = {f"P at {section}": [Point(e, 1.0, fraction * span)] for section, (e, fraction) in _SECTIONS.items()}
    solution = _cases.solve(_spans([span] * 3), loads)
    ordinates: list[float] = []
    for case in loads:
        m_b, m_c = solution[case].moment([span, 2 * span])
        ordinates += [100 * m_b / span, 100 * m_c / span]
    return ordinates


def _two_spans_9_3_1() -> list[float]:
    """§9.3.1, Fig. 9.8 (pp. 282-283): two equal spans, P at 0.4·l of the second.

    Force method: X₁ = −Δ₁P/δ₁₁ = **−0.096·P·l** over support 1 (p. 283); under P,
    0.24·P·l − 0.096·P·l·0.6 = **0.1824·P·l** (Fig. 9.8f); reactions **−0.096·P** (down),
    **0.792·P** and **0.304·P** (p. 283).

    Read here: M over support 1 and under P, over P·l; the reactions over P.
    """
    span, load = 5.0, 10.0
    result = _cases.solve(_spans([span, span]), {"P": [Point(1, load, 0.4 * span)]})["P"]
    m1, mn = result.moment([span, 1.4 * span])
    return [m1 / (load * span), mn / (load * span), *(result.reactions[:, 0] / load)]


def _three_spans_9_2() -> list[float]:
    """Example 9.2 (§9.3.2, pp. 287-289): three spans of 8 m, P = 15 kN at the middle of the
    second, q = 3 kN/m over the third.

    Three-moment equations (p. 289): **M₁ = −5.8 kN·m, M₂ = −21.8 kN·m**; reactions
    **R₀ = −0.725 kN** (down), **R₁ = 6.225 kN, R₂ = 24.225 kN, R₃ = 9.275 kN**.

    Read here: M over supports 1 and 2 (kN·m), the four reactions (kN).
    """
    result = _cases.solve(_spans([8.0] * 3), {"P + q": [Point(1, 15.0, 4.0), Uniform(2, 3.0)]})["P + q"]
    return [*result.moment([8.0, 16.0]), *result.reactions[:, 0]]


#: Example 9.3, the printed fractions: M₁, M₂, M₃ over P·l. As mako's net notes, the fraction
#: printed for M₃ is a slip for −(1/4)·3/(8·14), which is the printed decimal.
_FOUR_SPANS = (-45 / (32 * 14), 3 / (8 * 14), -3 / (8 * 14) / 4)


def _four_spans_9_3() -> list[float]:
    """Example 9.3 (§9.3.2, p. 290): four equal spans, P at the middle of the first.

    4·M₁ + M₂ = −3/8·P·l; M₁ + 4·M₂ + M₃ = 0; M₂ + 4·M₃ = 0, so **M₁ = −0.1004·P·l**,
    **M₂ = 0.0268·P·l**, **M₃ = −0.00670·P·l**.

    Read here: M over supports 1, 2 and 3, over P·l.
    """
    span, load = 4.0, 10.0
    result = _cases.solve(_spans([span] * 4), {"P": [Point(0, load, span / 2)]})["P"]
    return list(result.moment([span, 2 * span, 3 * span]) / (load * span))


CASES = [
    ValidationCase(
        name="karnovsky_13_6_1",
        source=f"{BOOK}, §13.6.1 pp. 471-473 and §10.2.4.1 pp. 374-376",
        run=_two_spans_13_6_1,
        expected=(6.163, -18.31, 21.475),
        rel=5e-4,
    ),
    ValidationCase(
        name="karnovsky_13_5_influence_lines",
        source=f"{BOOK}, Example 13.5, Fig. 13.25d p. 477 and Table 13.1 p. 479",
        run=_influence_lines_13_5,
        expected=tuple(value for pair in _ORDINATES for value in pair),
        rel=2e-3,
    ),
    ValidationCase(
        name="karnovsky_9_3_1",
        source=f"{BOOK}, §9.3.1, Fig. 9.8, pp. 282-283",
        run=_two_spans_9_3_1,
        expected=(-0.096, 0.1824, -0.096, 0.792, 0.304),
        rel=1e-6,
    ),
    ValidationCase(
        name="karnovsky_9_2",
        source=f"{BOOK}, Example 9.2, pp. 287-289",
        run=_three_spans_9_2,
        expected=(-5.8, -21.8, -0.725, 6.225, 24.225, 9.275),
        rel=1e-6,
    ),
    ValidationCase(
        name="karnovsky_9_3",
        source=f"{BOOK}, Example 9.3, p. 290",
        run=_four_spans_9_3,
        expected=_FOUR_SPANS,
        rel=1e-6,
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_karnovsky(case: ValidationCase) -> None:
    case.check()
