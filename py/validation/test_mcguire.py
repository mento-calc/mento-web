"""Beams by the stiffness method: McGuire, Gallagher and Ziemian, chapters 4 and 5.

Source: W. McGuire, R. H. Gallagher and R. D. Ziemian, *Matrix Structural Analysis*, 2nd edition,
John Wiley & Sons, New York, 2000:

- Example 4.9 (pp. 80-81): a beam of two members of different stiffness, fixed at one end, on a
  roller, with an overhang loaded at its tip.
- Example 4.10 (pp. 81-82): the same beam fixed at both ends, a couple at the roller.
- Table 5.1 (p. 111): the fixed-end forces of a point force, a couple and a partial uniform load.
- Example 5.6 (pp. 113-114): the same beam, pinned, on a roller and fixed, under a uniform load
  on one member and a point load on the other.

Transcribed from mako's validation net (``tests/validation/fem/test_space_frames_mcguire.py`` and
``test_loads_and_joints_mcguire.py``), which redid every number and documented the book's
rounding; the tolerances are theirs. Left out, because the solver does not take what they load:
Example 4.11 (a settlement), the triangular loads of Table 5.1, and every frame, grid and truss.

**Transcription.** The book draws x to the right and y up, with moments and rotations positive
anticlockwise: the solver's signs, so a rotation and a clamp's couple read as they are. Its moment
diagrams are drawn on the tension side, as the solver's sagging-positive moment. The beam of
Example 4.8 (p. 79): ab is 8 m with I = 200 × 10⁶ mm⁴, bc is 5 m with I = 50 × 10⁶ mm⁴,
E = 200 000 MPa, so EI = 40 000 and 10 000 kN·m². Units here: kN and m, the book's results in
its own (mm for a deflection).
"""

from __future__ import annotations

from math import sqrt

import pytest
from analysis import Beam, Couple, Point, Uniform

from . import _cases
from ._cases import ValidationCase

SOURCE = "McGuire, Gallagher & Ziemian, Matrix Structural Analysis, 2nd ed. (2000)"
#: The beam of Example 4.8: lengths (m) and EI (kN·m²) of ab and bc.
LENGTHS, STIFFNESS = (8.0, 5.0), (4.0e4, 1.0e4)
#: The book's three or four figures, from hand-rounded intermediate values.
BOOK = 5e-3
EXACT = 1e-6


def _example_4_9() -> list[float]:
    """Example 4.9 (pp. 80-81): fixed at a, a roller at b, bc an overhang; at its tip 5 kN down
    and to the right at 45°, whose vertical part 5/√2 kN is what bends the beam.

    **v_c = −19.15 mm**, **θ_zb = −0.00088 rad**, **θ_zc = −0.00530 rad**; reactions
    **R_ya = −3.30 kN**, **R_mza = −8.80 kN·m**, **R_yb = 6.85 kN**; the moment over b,
    **17.68 kN·m** drawn above the beam (hogging). By hand: the overhang hangs 5/√2·5 = 17.678 kN·m
    at b. The book's θ_zb has two figures and R_ya and R_mza are computed from it: 0.44 % off.

    Read here: v at c (mm), θ at b and c, the reactions at a (force, couple) and b, −M over b.
    """
    beam = Beam(LENGTHS, STIFFNESS, ("fixed", "pin", "free"))
    result = _cases.solve(beam, {"P": [Point(1, 5.0 / sqrt(2.0), 5.0)]})["P"]
    return [
        result.deflection(13.0) * 1e3,
        result.rotation(8.0),
        result.rotation(13.0),
        result.reactions[0, 0],
        result.reactions[0, 1],
        result.reactions[1, 0],
        -result.moment(8.0),
    ]


def _example_4_10() -> list[float]:
    """Example 4.10 (pp. 81-82): fixed at a and c, a roller at b, a couple of 50 kN·m
    anticlockwise at b.

    **θ_zb = 0.001786 rad**; **R_ya = 6.70 kN**, **R_mza = 17.86 kN·m**, **R_yb = −2.41 kN**,
    **R_yc = −4.29 kN**, **R_mzc = 7.14 kN·m**; the members' end moments at b,
    **M^ab_zb = 35.72 kN·m** and **M^bc_zb = 14.28 kN·m**: the couple splits by 4EI/L.

    Read here: θ at b; the reactions; the moment just before b (the node's anticlockwise
    couple on the end of ab) and minus the moment just past it (its couple on the start of bc).
    """
    beam = Beam(LENGTHS, STIFFNESS, ("fixed", "pin", "fixed"))
    result = _cases.solve(beam, {"C": [Couple(1, 50.0, 0.0)]})["C"]
    return [
        result.rotation(8.0),
        result.reactions[0, 0],
        result.reactions[0, 1],
        result.reactions[1, 0],
        result.reactions[2, 0],
        result.reactions[2, 1],
        result.moment(8.0),
        -result.moment(8.0, after=True),
    ]


def _table_5_1_expected() -> tuple[float, ...]:
    """Table 5.1's closed forms (p. 111), evaluated: L = 6 m, P = 30 kN and C = 20 kN·m at
    a = 2.5 m (b = 3.5 m); q = 10 kN/m over d = 1.5 m centred at 1.75 m. Each as
    (R₁, M₁, R₂, M₂): forces up, M₁ anticlockwise, M₂ clockwise. The couple is clockwise, and
    its signs come from equilibrium: the table's arrows for it do not close (mako's net)."""
    L, q, P, C = 6.0, 10.0, 30.0, 20.0
    a, b = 2.5, 3.5
    point = (P * b**2 * (3 * a + b) / L**3, P * a * b**2 / L**2, P * a**2 * (a + 3 * b) / L**3, P * a**2 * b / L**2)
    couple = (-6 * C * a * b / L**3, -C * b * (2 * a - b) / L**2, 6 * C * a * b / L**3, C * a * (2 * b - a) / L**2)
    d, a_p = 1.5, 1.75
    b_p = L - a_p
    partial = (
        q * d / L**3 * ((2 * a_p + L) * b_p**2 + (a_p - b_p) * d**2 / 4),
        q * d / L**2 * (a_p * b_p**2 + (a_p - 2 * b_p) * d**2 / 12),
        q * d / L**3 * ((2 * b_p + L) * a_p**2 - (a_p - b_p) * d**2 / 4),
        q * d / L**2 * (a_p**2 * b_p + (b_p - 2 * a_p) * d**2 / 12),
    )
    return point + couple + partial


def _table_5_1() -> list[float]:
    """Table 5.1 (p. 111): a beam of 6 m built in at both ends, each load its own case. The beam
    is two elements, 0-4 m and 4-6 m, with a free node between, so the solve has unknowns and
    the loads sit on either side of a node; built in at both ends, the reactions are the
    table's whatever that node does."""
    beam = Beam((4.0, 2.0), (1.0e4, 1.0e4), ("fixed", "free", "fixed"))
    loads = {
        "point": [Point(0, 30.0, 2.5)],
        "couple": [Couple(0, -20.0, 2.5)],
        "partial": [Uniform(0, 10.0, 1.0, 2.5)],
    }
    solution = _cases.solve(beam, loads)
    found: list[float] = []
    for case in loads:
        left, right = solution[case].reactions[0], solution[case].reactions[-1]
        found += [left[0], left[1], right[0], -right[1]]
    return found


def _example_5_6() -> list[float]:
    """Example 5.6 (pp. 113-114): a pinned, b on a roller, c built in; 2 kN/m down over ab and
    20 kN down on bc, 2 m from b.

    **θ_za = −5.684 × 10⁻⁴ rad**, **θ_zb = 0.698 × 10⁻⁴ rad** (0.3 % above its exact value, from
    the book's rounded fixed-end moments); **R_ya = 6.13 kN, R_yb = 23.00 kN, R_yc = 6.87 kN**,
    **R_mzc = −9.32 kN·m**; the beam hogs over b by **14.96 kN·m**, peaks at **9.39 kN·m** in ab
    at 3.065 m and at **11.29 kN·m** under the point load.

    Read here: θ at a and b; the reactions at a, b, c and the clamp's couple; M at b, at 3.065 m
    and under the load.
    """
    beam = Beam(LENGTHS, STIFFNESS, ("pin", "pin", "fixed"))
    result = _cases.solve(beam, {"5.6": [Uniform(0, 2.0), Point(1, 20.0, 2.0)]})["5.6"]
    return [
        result.rotation(0.0),
        result.rotation(8.0),
        *result.reactions[:, 0],
        result.reactions[2, 1],
        *result.moment([8.0, 3.065, 10.0]),
    ]


CASES = [
    ValidationCase(
        name="mcguire_4_9_overhang",
        source=f"{SOURCE}, Example 4.9, pp. 80-81",
        run=_example_4_9,
        expected=(-19.15, -0.00088, -0.00530, -3.30, -8.80, 6.85, 17.68),
        rel=BOOK,
    ),
    ValidationCase(
        name="mcguire_4_10_couple_at_support",
        source=f"{SOURCE}, Example 4.10, pp. 81-82",
        run=_example_4_10,
        expected=(0.001786, 6.70, 17.86, -2.41, -4.29, 7.14, 35.72, 14.28),
        rel=2e-3,
    ),
    ValidationCase(
        name="mcguire_table_5_1",
        source=f"{SOURCE}, Table 5.1, p. 111",
        run=_table_5_1,
        expected=_table_5_1_expected(),
        rel=EXACT,
    ),
    ValidationCase(
        name="mcguire_5_6",
        source=f"{SOURCE}, Example 5.6, pp. 113-114",
        run=_example_5_6,
        expected=(-5.684e-4, 0.698e-4, 6.13, 23.00, 6.87, -9.32, -14.96, 9.39, 11.29),
        rel=BOOK,
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_mcguire(case: ValidationCase) -> None:
    case.check()
