"""The page's example against CYPECAD: the same beam on the first floor and on the roof of a
two-storey frame, elastic and with CYPECAD's 15 % of redistribution.

Not a published source, so not in ``py/validation``: a contrast with the program engineers check
their designs in, whose numbers the owner of the site read off its diagrams (November 2026).

The frame: three spans of 5, 4 and 3 m between axes, on columns 30, 50, 40 and 30 cm along the
beam and 30 cm across, 3 m floor to floor, two storeys, pinned or fixed at the foundation; beams 30 × 50 cm,
HA-25 (Ec 27 264 MPa), rectangular, gross sections. Loads on both floors: their own weight, 6 kN/m
of CM and 4 kN/m of SCU. EHE-08, the envelope of 1.0 and 1.35 G with 0 and 1.5 Q, the live load
everywhere (CYPECAD does not pattern it). CYPECAD's options: its fixity coefficient of 0.3 on the
last storey's columns (their stiffness matrix interpolated, α·K_fixed-fixed + (1 − α)·K_pinned at
the head), joints of finite size, and its redistribution of the hogging moments: 15 % by default,
or none.

CYPECAD's redistribution, read off the same frame elastic and at 15 %: every face's hogging moment
times 0.85, each face of a column on its own, the end supports too, and the spans by equilibrium.
The page does the same (``analysis.redistribute`` with the faces).

Over each column CYPECAD rounds the moment: inside the column, the cubic tangent to the diagram at
both faces (at an end column, to nothing at its outer face). The hogging moment it writes over a
column is that curve's peak, between the faces, nearer the stiffer side; its eight labels of the
elastic runs come back from its own faces' moments and shears within 0.12 kN·m. The page draws and
shifts the same curve (``continuous.rounded``).

What CYPECAD prints at the faces of the columns: the shear, and each span's largest sagging
moment. The self-weight alone gives 3.68 kN/m in every span (0.30 · 0.50 · 24.5), which is how the
loads were confirmed: the faces' shears add up to the load on the clear span.

Rebuilt in PyNite, the frame of both storeys with joints of finite size both ways (the beam rigid
inside the column's width, the column inside the beam's depth) and free to sway lands within
0.3 kN of the first floor and 0.9 kN of the roof. The page solves one beam, which cannot see the
rest of the frame; it takes the same rigid joints, and each column as a spring at its axis: the
foundation pinned below the first floor; another floor's joint, turning as the beam's, at the far
end of every column that reaches one (6EI/L·(1 + 2a/L)²); the last storey interpolated as CYPECAD
does. Every run lands within 1.5 % of the largest shear; the moments within 1.5 % elastic and
2.5 % redistributed (the first floor on fixed foundations, 0.8 kN·m off).
"""

from __future__ import annotations

import json

import pytest

import continuous

#: CYPECAD's diagrams: shears at the faces (left and right of each span, kN), the largest sagging
#: moment of each span (kN·m), the largest moment of the diagram; the frame each one is, and how
#: close the page lands (of the largest shear, of the largest moment).
CYPECAD = {
    "first floor": {
        "V": (38.54, -49.17, 36.82, -30.86, 30.85, -19.68),
        "sag": (23.81, 9.00, 8.53),
        "M_max": 41.91,
        "frame": {"base": "pinned", "H_above": 3.0, "redistribution": 0},  # the column below on its foundation
        "tolerance": (0.015, 0.015),
    },
    "roof": {
        "V": (36.17, -51.53, 39.22, -28.46, 31.14, -19.39),
        "sag": (29.54, 7.13, 10.73),
        "M_max": 43.59,
        "frame": {"base": "floor", "H_above": 0.0, "redistribution": 0},  # the column below runs to the first floor
        "tolerance": (0.015, 0.015),
    },
    "first floor, 15 %": {
        "V": (39.34, -48.37, 36.37, -31.31, 30.01, -20.51),
        "sag": (27.65, 12.12, 9.61),
        "M_max": 36.15,
        "frame": {"base": "pinned", "H_above": 3.0, "redistribution": 15},
        "tolerance": (0.015, 0.025),
    },
    "first floor on fixed foundations, 15 %": {
        "V": (39.82, -47.89, 35.97, -31.71, 29.63, -20.89),
        "sag": (26.74, 12.40, 9.23),
        "M_max": 35.69,
        "frame": {"base": "fixed", "H_above": 3.0, "redistribution": 15},
        "tolerance": (0.015, 0.025),
    },
    "roof on fixed foundations, 15 %": {
        "V": (37.27, -50.43, 38.47, -29.22, 30.24, -20.29),
        "sag": (32.66, 10.38, 11.52),
        "M_max": 37.73,
        "frame": {"base": "floor", "H_above": 0.0, "redistribution": 15},
        "tolerance": (0.015, 0.025),
    },
}


def _beam(run: str) -> dict:
    data = json.loads(json.dumps(continuous.EXAMPLE))
    frame = CYPECAD[run]["frame"]
    data["base"] = frame["base"]
    data["above"] = "last"
    data["redistribution"] = frame["redistribution"]
    for column in data["columns"]:
        column["H_above"] = frame["H_above"]
    return data


def _readings(data: dict) -> tuple[list[float], list[float]]:
    """The envelope's shear at each face, and each span's largest sagging moment."""
    result = json.loads(continuous.analyse(json.dumps(data)))
    assert result["ok"], result
    diagrams = result["diagrams"]
    x, after = diagrams["x"], diagrams["after"]
    shears, sagging = [], []
    for element in result["elements"]:
        start, end = element["faces"]
        first = next(i for i, xi in enumerate(x) if abs(xi - start) < 1e-6 and after[i])
        last = next(i for i, xi in enumerate(x) if abs(xi - end) < 1e-6 and not after[i])
        shears += [diagrams["V_max"][first], diagrams["V_min"][last]]
        sagging.append(max(m for xi, m in zip(x, diagrams["M_max"], strict=True) if start <= xi <= end))
    return shears, sagging


def test_the_example_is_the_beam_compared() -> None:
    assert [span["L"] for span in continuous.EXAMPLE["spans"]] == [5.0, 4.0, 3.0]
    assert [column["width"] for column in continuous.EXAMPLE["columns"]] == [30, 50, 40, 30]
    assert continuous.EXAMPLE["code"] == "EN 1992-2004" and not continuous.EXAMPLE["pattern"]
    assert continuous.EXAMPLE["base"] == "pinned" and continuous.EXAMPLE["above"] == "last"


@pytest.mark.parametrize("run", list(CYPECAD))
def test_against_cypecad(run: str) -> None:
    shears, sagging = _readings(_beam(run))
    expected = CYPECAD[run]
    shear, moment = expected["tolerance"]
    largest = max(abs(value) for value in expected["V"])
    for ours, theirs in zip(shears, expected["V"], strict=True):
        assert abs(ours - theirs) <= shear * largest, (run, shears, expected["V"])
    for ours, theirs in zip(sagging, expected["sag"], strict=True):
        assert abs(ours - theirs) <= moment * expected["M_max"], (run, sagging, expected["sag"])


#: The hogging moment CYPECAD writes over B and C, elastic: the peak of its diagram rounded
#: inside the column, between the faces.
PEAKS = {"first floor": (-41.91, -19.19), "roof": (-43.59, -17.41)}


@pytest.mark.parametrize("run", list(PEAKS))
def test_the_peak_over_each_inner_column(run: str) -> None:
    """Rounded as CYPECAD rounds it, the peak over each inner column lands within 3 % of the
    largest moment of CYPECAD's: the faces' own difference, carried in."""
    result = json.loads(continuous.analyse(json.dumps(_beam(run))))
    diagrams = result["diagrams"]
    elements = result["elements"]
    for j, theirs in zip((1, 2), PEAKS[run], strict=True):
        left, right = elements[j - 1]["faces"][1], elements[j]["faces"][0]
        ours = min(m for x, m in zip(diagrams["x"], diagrams["M_min"], strict=True) if left <= x <= right)
        assert abs(ours - theirs) <= 0.03 * CYPECAD[run]["M_max"], (run, j, ours, theirs)


def test_the_self_weight_is_cypecad_s() -> None:
    """3.68 kN/m on every clear span, as CYPECAD's shears of the self-weight alone add up to."""
    data = _beam("roof") | {"loads": []}
    result = json.loads(continuous.analyse(json.dumps(data)))
    clear = [row["clear"] for row in result["deflections"]]
    shears, _ = _readings(data)
    for span, length in enumerate(clear):
        load = (shears[2 * span] - shears[2 * span + 1]) / length / 1.35  # the 1.35G combination
        assert load == pytest.approx(0.30 * 0.50 * 2500 * 9.80665 / 1000, rel=1e-3)
