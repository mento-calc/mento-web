"""Glue between the continuous beam page, the solver and mento: JSON in, JSON out.

The page sends the spans, their sections, the supports and the loads by case (dead ``D`` and live
``L``); ``py/analysis.py`` solves the beam, combines the cases as the code asks with the live load
span by span, and redistributes the moments over the supports; mento designs each span at its
critical sections, with what ``py/beam.py`` already does for one section. Runs inside Pyodide,
with no Pyodide-specific code::

    python -c "import continuous, json; print(continuous.run(json.dumps(continuous.EXAMPLE)))"

Units: spans, overhangs and positions in m (ft), sections and support widths in cm (in), cover
in mm (in), loads in kN/m and kN (kip/ft and kip), as the rest of the site.
"""

from __future__ import annotations

import functools
import json
import math
import warnings
from dataclasses import dataclass
from typing import Any

import analysis
import common
import mento
import numpy as np
from mento import Forces, Node, ureg

import beam


# The worked example every visitor opens is the case the page is checked against CYPECAD with
# (py/test_cypecad.py): the first floor's beam of a two-storey frame, 5, 4 and 3 m between axes on
# columns 30, 50, 40 and 30 cm wide, 3 m floor to floor, pinned at their foundation, the storey
# above the last; 30 × 50, its own weight, 6 kN/m of CM and 4 kN/m of SCU, EN 1992 with the
# factors of EHE-08. It passes with the governing DCR between 0.7 and 0.99.
def _columns_of(widths: list[float], depth: float, height: float) -> list[dict[str, Any]]:
    return [{"width": width, "depth": depth, "H_below": height, "H_above": height, "pinned": False} for width in widths]


EXAMPLE: dict[str, Any] = {
    "code": "EN 1992-2004",
    "units": "si",
    "lang": "es",
    "label": "V-101",
    "fc": 25,
    "fy": 500,
    "cover": 30,
    "spans": [{"L": 5.0, "width": 30, "height": 50}, {"L": 4.0, "width": 30, "height": 50}, {"L": 3.0, "width": 30, "height": 50}],
    "ends": {"left": {"type": "column"}, "right": {"type": "column"}},
    "columns": _columns_of([30, 50, 40, 30], 30, 3.0),
    "head_fixity": 0.3,
    "base": "pinned",
    "above": "last",
    "loads": [{"case": case, "type": "uniform", "element": e, "value": value}
              for case, value in (("SDL", 6.0), ("L", 4.0)) for e in range(3)],
    "self_weight": True,
    "pattern": False,
    "deflection_factor": 3.0,
    "redistribution": 15,
}  # fmt: skip
EXAMPLE_US: dict[str, Any] = {
    "code": "ACI 318-19",
    "units": "us",
    "lang": "en",
    "label": "B-101",
    "fc": 4000,
    "fy": 60,
    "cover": 1.5,
    "spans": [{"L": 16.0, "width": 12, "height": 20}, {"L": 13.0, "width": 12, "height": 20}, {"L": 10.0, "width": 12, "height": 20}],
    "ends": {"left": {"type": "column"}, "right": {"type": "column"}},
    "columns": _columns_of([12, 20, 16, 12], 12, 10.0),
    "head_fixity": 0.3,
    "base": "pinned",
    "above": "last",
    "loads": [{"case": case, "type": "uniform", "element": e, "value": value}
              for case, value in (("SDL", 0.5), ("L", 0.5)) for e in range(3)],
    "self_weight": True,
    "pattern": False,
    "deflection_factor": 3.0,
    "redistribution": 15,
}  # fmt: skip

#: The most spans a beam may have: with an overhang at each end the live load has 2¹⁰ arrangements.
MAX_SPANS = 8
#: Load factors of the ultimate combinations: (label, permanent, live). The permanent load is the
#: dead load D (with the self-weight) and the superimposed dead load SDL; under EN 1992 they are
#: PP and CM, and the live load SCU. ACI 318-19 §5.3.1 and CIRSOC 201-25: 1.4D, and 1.2D + 1.6L.
#: EN 1990 eq. (6.10), the four EHE-08 writes out: G favourable or not (1.0, 1.35), Q absent or
#: leading (0, 1.5). The design takes the envelope of them all.
COMBINATIONS = {
    "ACI 318-19": [("1.4D", 1.4, 0.0), ("1.2D+1.6L", 1.2, 1.6)],
    "CIRSOC 201-25": [("1.4D", 1.4, 0.0), ("1.2D+1.6L", 1.2, 1.6)],
    "EN 1992-2004": [("1.0G", 1.0, 0.0), ("1.35G", 1.35, 0.0), ("1.0G+1.5Q", 1.0, 1.5), ("1.35G+1.5Q", 1.35, 1.5)],
}
#: The deflection is the instantaneous one under the service load, D + SDL + L, on the gross
#: section, times a multiplier for cracking and creep (3 unless the page says otherwise), over the
#: clear span between the faces of the columns: against clear span/250, an overhang 2·length/250.
#: It is shown, never part of the verdict.
DEFLECTION_LIMIT = 250.0
DEFLECTION_FACTOR = 3.0
#: The last storey's column is not held at its head as a floor holds it. CYPECAD's "coeficiente de
#: empotramiento en última planta", 0.3 by default, interpolates its stiffness matrix between the
#: fixed-fixed bar's and the bar pinned at its head: K = α·K_ff + (1 − α)·K_fp.
HEAD_FIXITY = 0.3
#: Where the column below ends: on its foundation, pinned (the default) or fixed, or on another
#: floor, whose joint turns as this one does. The column above is the last storey's (its head under
#: the roof) or not.
BASES = ("pinned", "fixed", "floor")
ABOVE = ("last", "intermediate")
#: ACI 318-19 §6.6.5.1 and CIRSOC 201-25: no redistribution below εt = 0.0075, at most 1000·εt %,
#: never past 20 %.
ACI_EPSILON_MIN, ACI_MAX_PERCENT = 0.0075, 20.0
#: EN 1992-1-1 §5.5(4): δ ≥ k₁ + k₂·x_u/d and δ ≥ k₅ = 0.7 (Class B and C reinforcement), with
#: adjacent spans between 0.5 and 2 times each other.
EN_DELTA_MIN, EN_SPAN_RATIO = 0.70, 2.0
#: What only mento's design says, and its check of the same bars does not: py/beam.py's shortfall,
#: and a face that cannot keep the δ of EN 1992-1-1 §5.5(4) it was designed for (mento 1.7.0).
DESIGN_ONLY_CODES = (*beam._SHORTFALL_CODES, "x_u_above_redistribution_limit")
#: Below this, a moment is zero: 0.01 kN·m on a beam.
_NOTHING = 1e-6

LENGTH = {"si": "m", "us": "ft"}
WORDS = {
    "es": {"span": "Vano {n}", "left": "Voladizo izq.", "right": "Voladizo der.", "M+": "M+ tramo", "M-l": "M− apoyo izq.",
           "M-r": "M− apoyo der.", "M-o": "M− apoyo"},
    "en": {"span": "Span {n}", "left": "Left overhang", "right": "Right overhang", "M+": "M+ span", "M-l": "M− left support",
           "M-r": "M− right support", "M-o": "M− support"},
}  # fmt: skip


# ----------------------------------------------------------------------------------- the inputs


@dataclass(frozen=True)
class Element:
    """A span or an overhang as the page sent it."""

    kind: str  # "span", "left" or "right"
    name: str
    length: float  # m or ft
    width: float  # cm or in
    height: float


def _number(value: Any, field: str, *, positive: bool = True) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise common.InputError(field, "missing") from None
    if not math.isfinite(number) or (positive and number <= 0) or number < 0:
        raise common.InputError(field, "positive")
    return number


def _elements(data: dict[str, Any]) -> tuple[list[Element], tuple[str, ...]]:
    """The elements in order along the beam, the overhangs at their ends, and the support at
    each node."""
    words = WORDS.get(data.get("lang"), WORDS["en"])
    spans = data.get("spans") or []
    if not spans:
        raise common.InputError("spans", "empty")
    if len(spans) > MAX_SPANS:
        raise common.InputError("spans", "max")
    ends = data.get("ends") or {}
    elements: list[Element] = []
    kinds: list[str] = []

    def element(kind: str, name: str, values: dict[str, Any], field: str) -> Element:
        return Element(
            kind,
            name,
            _number(values.get("L"), field),
            _number(values.get("width"), field),
            _number(values.get("height"), field),
        )

    # An end is a column (held by its stiffness, the default), an overhang, or one of the ideal
    # supports the tests use: a pin, or a fixed end.
    left, right = ends.get("left") or {"type": "column"}, ends.get("right") or {"type": "column"}
    for end in (left, right):
        if end.get("type") not in ("column", "pin", "fixed", "overhang"):
            raise common.InputError("ends", "unknown")
    if left["type"] == "overhang":
        elements.append(element("left", words["left"], left, "ends"))
        kinds.append("free")
    kinds.append("fixed" if left["type"] == "fixed" else "pin")
    for index, span in enumerate(spans):
        elements.append(element("span", words["span"].format(n=index + 1), span, "spans"))
        kinds.append("pin")
    kinds[-1] = "fixed" if right["type"] == "fixed" else "pin"
    if right["type"] == "overhang":
        elements.append(element("right", words["right"], right, "ends"))
        kinds.append("free")
    return elements, tuple(kinds)


def _loads(data: dict[str, Any], elements: list[Element], weight: list[float]) -> dict[str, list[analysis.Load]]:
    """The loads by case: ``D`` (with the self-weight) and ``SDL`` on the whole beam, and the live
    load of each element in a case of its own, ``L@<element>``, so that it can be patterned."""
    cases: dict[str, list[analysis.Load]] = {"D": [], "SDL": []}
    if data.get("self_weight", True):
        cases["D"] += [analysis.Uniform(e, w) for e, w in enumerate(weight)]
    for row in data.get("loads") or []:
        case = row.get("case")
        if case not in ("D", "SDL", "L"):
            raise common.InputError("loads", "case")
        try:
            e = int(row.get("element"))
        except (TypeError, ValueError):
            raise common.InputError("loads", "element") from None
        if not 0 <= e < len(elements):
            raise common.InputError("loads", "element")
        value = _number(row.get("value"), "loads", positive=False)
        if value == 0:
            continue
        L = elements[e].length
        if row.get("type") == "uniform":
            a = _number(row.get("a") if row.get("a") is not None else 0.0, "loads", positive=False)
            b = _number(row.get("b") if row.get("b") is not None else L, "loads", positive=False)
            if not 0 <= a < b <= L * (1 + 1e-9):
                raise common.InputError("loads", "position")
            load: analysis.Load = analysis.Uniform(e, value, a, min(b, L))
        elif row.get("type") == "point":
            a = _number(row.get("a"), "loads", positive=False)
            if not 0 <= a <= L * (1 + 1e-9):
                raise common.InputError("loads", "position")
            load = analysis.Point(e, value, min(a, L))
        else:
            raise common.InputError("loads", "type")
        cases.setdefault(case if case != "L" else f"L@{e}", []).append(load)
    return cases


# --------------------------------------------------------------------------------- the analysis


@dataclass(frozen=True)
class Analysis:
    """The beam solved: the elastic and redistributed ultimate arrangements, the service ones,
    and where each support's faces are."""

    elements: list[Element]
    beam: analysis.Beam
    solution: analysis.Solution
    ultimate: list[analysis.Result]  # elastic, every arrangement of every combination
    design: list[analysis.Result]  # the same, redistributed
    service: list[analysis.Result]
    labels: list[str]  # the combination of each ultimate arrangement
    ratio: float
    faces: list[tuple[float, float]]  # each element's left and right face along the beam
    deflection_factor: float
    self_weight: list[float]  # each element's own weight, per length


def column_stiffness(EI: float, clear: float, arm: float, far: str) -> float:
    """The rotational stiffness a column holds a joint with, through a rigid arm of length ``arm``
    (the part of the column inside the beam's depth, from the beam's axis to its face) and a
    flexible length ``clear``:

    - ``fixed`` far end (a foundation): 4EI/L·(1 + 3a/L + 3a²/L²);
    - ``pinned`` far end: 3EI/L·(1 + a/L)²;
    - ``floor``, another floor's joint at the far end, with its own arm, turning as this one does
      (the floors of a frame under gravity bend their columns in double curvature): 6EI/L·(1 + 2a/L)².
    """
    ratio = arm / clear
    if far == "fixed":
        return 4 * EI / clear * (1 + 3 * ratio + 3 * ratio**2)
    if far == "floor":
        return 6 * EI / clear * (1 + 2 * ratio) ** 2
    return 3 * EI / clear * (1 + ratio) ** 2


def _columns(
    data: dict[str, Any], supports: tuple[str, ...], elements: list[Element], modulus: Any
) -> tuple[list, list, list]:
    """Each support's column: its width along the beam (where the faces are), the rotational
    stiffness it holds the beam with, and whether it holds it rigidly, as CYPECAD's frame does with
    its joints of finite size: the beam rigid inside the column's width, the column rigid inside
    the beam's depth.

    Each column is a flexible length, floor to floor less the beam's depth, and a rigid arm from
    the beam's axis to its face (half its depth), the joint of finite size.

    - The column below ends as ``base`` says: on its foundation, pinned (the default) or fixed, or
      on another floor (``floor``), turning as this one.
    - Under a roof (no column above) it is the last storey's column, seen from its head:
      α·K_ff + (1 − α)·K_fp, and the share pinned at the head holds nothing there.
    - The column above is the last storey's (``above="last"``, seen from its foot: α of it bending
      with the roof's joint, the rest pinned at its head) or an intermediate one, bending with the
      next floor's joint.

    A pinned column holds nothing, and the beam turns freely on it. Without columns (a payload from before them) every support is
    ``support_width`` wide and holds nothing."""
    units = common.units_of(data)
    section, span = ureg(units.length), ureg(LENGTH[units.name])
    held = [j for j, kind in enumerate(supports) if kind != "free"]
    columns = data.get("columns")
    if columns is None:
        width = _number(data.get("support_width") or 0, "support_width", positive=False)
        return [width if kind != "free" else 0.0 for kind in supports], [0.0] * len(supports), [False] * len(supports)
    if len(columns) != len(held):
        raise common.InputError("columns", "count")
    alpha = _number(data.get("head_fixity", HEAD_FIXITY), "head_fixity", positive=False)
    base = data.get("base", "pinned")
    above_kind = data.get("above", "last")
    if base not in BASES or above_kind not in ABOVE:
        raise common.InputError("columns", "unknown")
    unit = f"{units.force}*{LENGTH[units.name]}"
    widths, springs, joints = [0.0] * len(supports), [0.0] * len(supports), [False] * len(supports)
    for j, column in zip(held, columns, strict=True):
        a = _number(column.get("width"), "columns")
        b = _number(column.get("depth"), "columns")
        widths[j] = a
        if column.get("pinned"):
            continue
        joints[j] = True
        if supports[j] == "fixed":
            continue
        # the beam's depth at the column: the deeper of the elements that meet there
        depth = max(elements[e].height for e in (j - 1, j) if 0 <= e < len(elements)) * section
        EI = float(
            (modulus * b * section * (a * section) ** 3 / 12).to(f"{units.force}*{LENGTH[units.name]}**2").magnitude
        )
        arm = float((depth / 2).to(span).magnitude)
        clears = {}
        for key in ("H_below", "H_above"):
            height = _number(column.get(key) or 0, "columns", positive=False) * span
            if height.magnitude == 0:
                continue
            clear = float((height - depth).to(span).magnitude)
            if clear <= 0:
                raise common.InputError("columns", "height")
            clears[key] = clear
        k = 0.0
        if "H_below" in clears:
            below = column_stiffness(EI, clears["H_below"], arm, base)
            k += alpha * below if "H_above" not in clears else below
        if "H_above" in clears:
            bending = column_stiffness(EI, clears["H_above"], arm, "floor")
            pinned = column_stiffness(EI, clears["H_above"], arm, "pinned")
            k += alpha * bending + (1 - alpha) * pinned if above_kind == "last" else bending
        springs[j] = float(k * ureg(unit).to(unit).magnitude)
    return widths, springs, joints


def _analyse(data: dict[str, Any]) -> Analysis:
    units = common.units_of(data)
    span = LENGTH[units.name]
    concrete, _ = common.materials(data)
    elements, supports = _elements(data)
    modulus = concrete.E_cm if data["code"] == "EN 1992-2004" else concrete.E_c
    section = ureg(units.length)
    stiffness, weight = [], []
    for element in elements:
        b, h = element.width * section, element.height * section
        stiffness.append(float((modulus * b * h**3 / 12).to(f"{units.force}*{span}**2").magnitude))
        weight.append(float((concrete.density * ureg.gravity * b * h).to(f"{units.force}/{span}").magnitude))
    widths, springs, joints = _columns(data, supports, elements, modulus)
    # the beam rigid inside the width of each column it is cast with
    half = [
        float((width / 2 * section).to(span).magnitude) if joint else 0.0
        for width, joint in zip(widths, joints, strict=True)
    ]
    rigid = tuple((half[e], half[e + 1]) for e in range(len(elements)))
    if any(a + b >= 0.9 * element.length for (a, b), element in zip(rigid, elements, strict=True)):
        raise common.InputError("columns", "too_wide")
    try:
        model = analysis.Beam(
            tuple(e.length for e in elements), tuple(stiffness), supports, springs=tuple(springs), rigid=rigid
        )
        solution = analysis.solve(model, _loads(data, elements, weight))
    except analysis.AnalysisError as error:
        raise common.InputError(error.field if error.field != "lengths" else "spans", str(error)) from None

    ratio = _number(data.get("redistribution") or 0, "redistribution", positive=False) / 100
    if ratio >= 0.3 + 1e-9:  # no code goes past 30 %: EN's δ ≥ 0.7
        raise common.InputError("redistribution", "max")
    live = [case for case in solution.cases if case.startswith("L@")]
    pattern = bool(data.get("pattern"))

    def combination(dead: float, factor: float) -> list[analysis.Result]:
        """The live load span by span when patterned (every arrangement), or everywhere at once."""
        permanent = {"D": dead, "SDL": dead}
        if pattern and factor:
            return [found.result for found in analysis.arrangements(solution, permanent, dict.fromkeys(live, factor))]
        return [solution.combine(permanent | dict.fromkeys(live, factor))]

    ultimate, labels = [], []
    for label, dead, factor in COMBINATIONS[data["code"]]:
        found = combination(dead, factor)
        ultimate += found
        labels += [label] * len(found)
    design = [analysis.redistribute(result, ratio, faces=half) if ratio else result for result in ultimate]
    service = combination(1.0, 1.0)

    faces = []
    for e, element in enumerate(elements):
        x0, x1 = model.nodes[e], model.nodes[e + 1]
        start = x0 + float((widths[e] / 2 * section).to(span).magnitude)
        end = x1 - float((widths[e + 1] / 2 * section).to(span).magnitude)
        if end - start <= 0.1 * element.length:
            raise common.InputError("columns" if data.get("columns") is not None else "support_width", "too_wide")
        faces.append((float(start), float(end)))
    factor = _number(data.get("deflection_factor", DEFLECTION_FACTOR), "deflection_factor")
    return Analysis(elements, model, solution, ultimate, design, service, labels, ratio, faces, factor, weight)


def _at(results: list[analysis.Result], x: float, after: bool) -> np.ndarray:
    """V and M of every result at one section, shape (results, 2)."""
    return analysis.diagrams(results, np.array([x]), np.array([after]))[:, 0, :2]


def _round(values: Any, digits: int = 4) -> list[float]:
    return [round(float(value), digits) for value in np.asarray(values).ravel()]


def effective_depth(data: dict[str, Any], element: Element) -> float:
    """d, in the span's unit, estimated before the bars are known: the depth less the cover, a
    stirrup of 8 mm (#3) and half a bar of 16 mm (#5). Where the code reads the shear (ACI 318-19
    §9.4.3.2, EN 1992-1-1 §6.2.1(8)): at d from the face of the support."""
    units = common.units_of(data)
    cover = _number(data.get("cover"), "cover") * ureg(units.cover)
    bars = (0.8 + 0.8) * ureg.cm if not units.us else (0.375 + 0.3125) * ureg.inch
    depth = element.height * ureg(units.length) - cover - bars
    return max(float(depth.to(LENGTH[units.name]).magnitude), 0.0)


def _demands(study: Analysis, e: int, d: float) -> dict[str, tuple[float, float]]:
    """The critical sections of an element, as (M, |V|) pairs: the largest sagging moment along
    it, and over each support its largest hogging moment, with the largest shear at d from its
    face. Over a column the beam is cast with, the hogging moment is the peak of the moment rounded
    inside it (:func:`rounded`), as CYPECAD designs the top steel: never less than either face's
    (ACI 318-19 §9.4.2.1, EN 1992-1-1 §5.3.2.2(3) would allow the face). Over a support without a
    rigid joint, the larger of its faces'."""
    x, after = analysis.stations(study.design)
    start, end = study.faces[e]
    inside = (x >= start) & (x <= end)
    bounds = analysis.envelope(study.design, x[inside], after[inside])
    demands: dict[str, tuple[float, float]] = {"M+": (max(float(bounds.M_max.max()), 0.0), 0.0)}
    kinds = study.beam.supports
    reach = min(d, (end - start) / 2)
    nodes = study.beam.nodes
    columns = _columns_of(study)
    for key, at, past, node, toward in (("M-l", start, True, e, 1), ("M-r", end, False, e + 1, -1)):
        if kinds[node] == "free":
            continue
        shear = _at(study.design, at + toward * reach, past)[:, 0]
        column = next(((a, b) for a, b in columns if a < nodes[node] < b), None)
        if column is not None:
            peak = _peak(study, column)
            demands[key] = (min(peak, float(_at(study.design, at, past)[:, 1].min()), 0.0), float(np.abs(shear).max()))
            continue
        # the other face of the same column, when the beam goes on past it
        other = [
            (study.faces[k][side], side == 0)
            for k, side in ((node - 1, 1), (node, 0))
            if 0 <= k < len(study.faces)
            and abs(study.faces[k][side] - at) > 1e-9
            and min(abs(study.faces[k][side] - nodes[node]), abs(at - nodes[node])) < (end - start)
        ]
        moment = np.minimum.reduce(
            [_at(study.design, at, past)[:, 1]] + [_at(study.design, x, after)[:, 1] for x, after in other]
        )
        demands[key] = (min(float(moment.min()), 0.0), float(np.abs(shear).max()))
    if study.elements[e].kind != "span":  # an overhang: one support, its moment is the support's
        demands["M-o"] = demands.pop("M-r" if study.elements[e].kind == "left" else "M-l")
    return demands


def _peak(study: Analysis, column: tuple[float, float]) -> float:
    """The most hogging moment over a column, the moment rounded inside it, of every result."""
    a, b = column
    length = study.beam.length
    inside = np.linspace(a, b, 81)[1:-1]
    inside = inside[(inside >= 0.0) & (inside <= length)]
    x = np.concatenate([inside, [a] if a >= 0 else [], [b] if b <= length else []])
    after = np.concatenate([np.zeros(inside.size, bool), [False] if a >= 0 else [], [True] if b <= length else []])
    return float(rounded(study.design, x, after.astype(bool), [column]).min())


# ----------------------------------------------------------------------------------- the design


def _element_data(data: dict[str, Any], element: Element, ratio: float) -> dict[str, Any]:
    """What ``py/beam.py`` takes for one section: this element's, in this beam's materials.
    Under EN the redistribution sets δ (§5.5(4)): mento's own 0.85 up to 15 %, 1 − r past it."""
    section = {key: data.get(key) for key in ("code", "units", "lang", "fc", "fy", "cover")}
    # the beam's label, not the span's name: two spans alike are one design, from the cache
    label = common.label_of(data, "V")
    section |= {"label": label, "width": element.width, "height": element.height, "mode": "design"}
    if data.get("code") == "EN 1992-2004" and ratio > 0.15:
        section["delta"] = round(1.0 - ratio, 6)
    return section


@functools.lru_cache(maxsize=128)
def _design(payload: str) -> dict[str, Any]:
    """One section designed and checked under its critical forces: what the page shows of it.
    Cached by its inputs, so a span whose section and forces did not change is not designed again
    while the visitor edits another."""
    data = json.loads(payload)
    units = common.units_of(data)
    forces = common.build_forces(data)
    designed = beam._build_beam(data)
    Node(section=designed, forces=forces).design()
    # the layout mento applied, without the alternatives the beam page offers (none to pick here)
    shear_options = designed.shear_design.options
    st = beam._stirrup_layout(shear_options[0] if shear_options else designed.reinforcement.transverse, units)
    flexure_design = designed.flexure_design
    layouts = {
        group: beam._layers_layout(data, st, group, (face.options[0].layers if face.options else face.layers))
        for group, face in (("bot", flexure_design.bottom), ("top", flexure_design.top))
    } | {"st": st}
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        checked, node = beam._checked(data, forces, layouts)
    flexure, shear = checked.flexure_design, checked.shear_design
    complies = bool(flexure.complies) and shear.DCR <= 1
    notices = common.warning_notices(node, list(captured))
    # Only a design can say that no layout that fits keeps the face within the δ it was designed
    # for (EN 1992-1-1 §5.5(4)): mento raises it on the beam it designed, never on one checked
    # with the same bars. The redistribution check of the support then fails on its own.
    known = {notice["code"] for notice in notices}
    notices += [
        notice
        for notice in common.warning_notices(designed, [])
        if notice["code"] in DESIGN_ONLY_CODES and notice["code"] not in known
    ]
    return {
        "rebar": {group: beam._layout_bars(layout, units) for group, layout in layouts.items()},
        "rows": {group: beam._layout_rows(layout, units) for group, layout in layouts.items()},
        "layouts": layouts,
        "flexure": {"bottom": beam._face(flexure.bottom, units), "top": beam._face(flexure.top, units)},
        # what each face resists, in the beam's moment unit: each critical section's own DCR
        "capacity": {
            face: float(getattr(flexure, face).M_capacity.to(f"{units.force}*{LENGTH[units.name]}").magnitude)
            for face in ("bottom", "top")
        },
        "shear": {"stirrups": beam._layout_bars(layouts.get("st") or {}, units), "DCR": round(float(shear.DCR), 3)},
        "complies": complies,
        # how ductile the top face is, which the redistribution over a support reads: None with a
        # mento that does not report it yet (before 1.7.0)
        "epsilon_t": getattr(flexure.top, "epsilon_t", None),
        "x_u_d": getattr(flexure.top, "x_u_d", None),
        "notices": notices,
        "section": beam._section(checked, layouts, units),
    }


#: Where the profiles of the steel are read along each element, between its faces.
PROFILE_STATIONS = 9


def _quantity(value: Any, unit: str) -> float | None:
    return None if value is None else round(float(value.to(unit).magnitude), 3)


@functools.lru_cache(maxsize=128)
def _profile(payload: str) -> dict[str, Any]:
    """The steel a span needs and has along it: the envelope at each station enters mento as one
    combination of a single check of the designed section, sagging for the bottom face, hogging
    for the top, the shear with each. ``beam.flexure_checks`` and ``beam.shear_checks`` give the
    required areas; the provided ones are the section's."""
    data = json.loads(payload)
    units = common.units_of(data)
    system = "imperial" if units.us else "metric"
    force, moment = ureg(units.force), ureg(units.moment)
    forces = []
    for i, (sag, hog, shear) in enumerate(data["stations"]):
        forces.append(Forces(label=f"b{i}", M_y=sag * moment, V_z=shear * force, N_x=0 * force, unit_system=system))
        forces.append(Forces(label=f"t{i}", M_y=hog * moment, V_z=shear * force, N_x=0 * force, unit_system=system))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        checked, _ = beam._checked(data, forces, data["layouts"])
    area, run = units.labels["area_unit"], units.labels["per_length_unit"]
    flexure = {check.label: check for check in checked.flexure_checks}
    shear = {check.label: check for check in checked.shear_checks}
    count = len(data["stations"])
    design = checked.flexure_design

    def spacing(check: Any) -> float | None:
        """The widest the stirrups may be spaced there, in the section's unit."""
        limits = [value for value in (check.s_max_l_table, check.s_max_w) if value is not None]
        return _quantity(min(limits), units.length) if limits else None

    return {
        # what the moment asks for, before the minimum, which is a line of its own
        "As_bot_req": [_quantity(flexure[f"b{i}"].bottom.A_s_calc, area) or 0.0 for i in range(count)],
        "As_top_req": [_quantity(flexure[f"t{i}"].top.A_s_calc, area) or 0.0 for i in range(count)],
        "As_bot_min": _quantity(design.bottom.A_s_min_eff, area),
        "As_top_min": _quantity(design.top.A_s_min_eff, area),
        "Av_req": [_quantity(shear[f"b{i}"].A_v_req, run) or 0.0 for i in range(count)],
        "s_max": [spacing(shear[f"b{i}"]) for i in range(count)],
        "As_bot": _quantity(design.bottom.A_s, area),
        "As_top": _quantity(design.top.A_s, area),
        "Av": _quantity(checked.shear_design.A_v, run),
    }


def _layout_av(layout: dict[str, Any], units: common.Units) -> float | None:
    """The area of a stirrup layout per length, in cm²/m (in²/ft)."""
    if not layout or not layout.get("n"):
        return None
    return round(layout["n"] * units.bar_area(layout["d"]) * units.run / layout["s"], 3)


def _zones(data: dict[str, Any], study: Analysis, e: int, design: dict[str, Any]) -> list[dict[str, Any]]:
    """The stirrups of a span in three zones. A quarter of the clear span from each face takes
    the stirrups mento designed for the shear at d from the face; the middle takes the same
    stirrup, spaced as the largest Av it needs there asks, never past the code's widest spacing
    there nor closer than at the ends. An overhang is one zone."""
    units = common.units_of(data)
    start, end = study.faces[e]
    edge = design["layouts"].get("st") or {}
    ends = {"bars": design["rebar"]["st"], "Av": _layout_av(edge, units), "s": edge.get("s")}
    if study.elements[e].kind != "span" or not edge.get("n"):
        return [{"x0": round(start, 4), "x1": round(end, 4)} | ends]
    quarter = (end - start) / 4
    profile = design["profile"]
    inside = [i for i, x in enumerate(profile["x"]) if start + quarter <= x <= end - quarter]
    need = max((profile["Av_req"][i] for i in inside), default=0.0)
    widest = min((profile["s_max"][i] for i in inside if profile["s_max"][i]), default=edge["s"])
    legs = edge["n"] * units.bar_area(edge["d"]) * units.run
    step = 0.5 if units.us else 1.0
    s = min(legs / need if need > 0 else widest, widest)
    s = max(math.floor(s / step) * step, edge["s"])
    middle = {"n": edge["n"], "d": edge["d"], "s": s}
    return [
        {"x0": round(start, 4), "x1": round(start + quarter, 4)} | ends,
        {
            "x0": round(start + quarter, 4),
            "x1": round(end - quarter, 4),
            "bars": beam._layout_bars(middle, units),
            "Av": _layout_av(middle, units),
            "s": s,
        },
        {"x0": round(end - quarter, 4), "x1": round(end, 4)} | ends,
    ]


def _support_faces(study: Analysis) -> list[tuple[float, float]]:
    """Each support's two faces along the beam: the end of the element before it and the start
    of the one after (an end support has the beam on one side only)."""
    nodes = study.beam.nodes
    found = []
    for j, kind in enumerate(study.beam.supports):
        if kind == "free":
            continue
        left = study.faces[j - 1][1] if j > 0 else float(nodes[j])
        right = study.faces[j][0] if j < len(study.faces) else float(nodes[j])
        found.append((left, right))
    return found


def _columns_of(study: Analysis) -> list[tuple[float, float]]:
    """Each column the beam is cast with (a rigid joint): where its two faces are. An end column
    reaches past the end of the beam's axis, where the beam stops at its outer face."""
    beam = study.beam
    count = len(beam.lengths)
    found = []
    for j, kind in enumerate(beam.supports):
        if kind == "free":
            continue
        half = max(beam.ends(j - 1)[1] if j > 0 else 0.0, beam.ends(j)[0] if j < count else 0.0)
        if half > 0:
            found.append((float(beam.nodes[j] - half), float(beam.nodes[j] + half)))
    return found


def rounded(results: list[analysis.Result], x: np.ndarray, after: np.ndarray, columns: list) -> np.ndarray:
    """The moment of each result at each section, shape (results, sections), rounded inside each
    column as CYPECAD draws it: the cubic tangent to the diagram at both faces (at an end column,
    to no moment and no shear at its outer face). Its peak is the hogging moment over the column,
    between the faces and wherever the stiffness on each side puts it, never the axis's jump."""
    moments = analysis.diagrams(results, x, after)[..., 1]
    length = results[0].layout.beam.length

    def face(point: float, side: bool) -> tuple[np.ndarray, np.ndarray]:
        if point < -1e-9 or point > length + 1e-9:  # past the end of the beam: nothing
            return np.zeros(len(results)), np.zeros(len(results))
        values = analysis.diagrams(results, np.array([point]), np.array([side]))[:, 0]
        return values[:, 1], values[:, 0]

    for a, b in columns:
        inside = (x > a + 1e-9) & (x < b - 1e-9)
        if not inside.any():
            continue
        (Ma, Va), (Mb, Vb) = face(a, False), face(b, True)
        t = b - a
        u = (x[inside] - a) / t
        h00, h10 = 2 * u**3 - 3 * u**2 + 1, u**3 - 2 * u**2 + u
        h01, h11 = -2 * u**3 + 3 * u**2, u**3 - u**2
        moments[:, inside] = np.outer(Ma, h00) + np.outer(t * Va, h10) + np.outer(Mb, h01) + np.outer(t * Vb, h11)
    return moments


def shifted(study: Analysis, depths: list[float], x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The moment envelope shifted by d to each side (the "decalaje" of EHE-08 and EN 1992-1-1
    §9.2.1.3, ACI 318-19 §9.7.3.3's d past the point a bar is no longer needed), over the whole
    beam, inside the columns too: the hogging peak over each column, rounded as CYPECAD rounds it
    (:func:`rounded`), is carried d to each side of it. At each section, the largest and the
    smallest of the envelope within d of it, d the depth of the element there (the deeper of the
    two at a column). The design of a section stays at the face; the shift tells how far the bars
    must run."""
    nodes = study.beam.nodes
    stations, after = analysis.stations(study.design, per_element=48)
    supports = _support_faces(study)
    edges = np.array([face for pair in supports for face in pair])
    grid = np.concatenate([stations, edges, edges, x])
    sides = np.concatenate([after, np.zeros(edges.size, bool), np.ones(edges.size, bool), np.zeros(x.size, bool)])
    moments = rounded(study.design, grid, sides, _columns_of(study))
    high, low = moments.max(0), moments.min(0)

    def depth_at(point: float) -> float:
        e = int(np.clip(np.searchsorted(nodes, point, side="right") - 1, 0, len(depths) - 1))
        near = [
            depths[k]
            for k in (e - 1, e, e + 1)
            if 0 <= k < len(depths) and nodes[k] - 1e-9 <= point <= nodes[k + 1] + 1e-9
        ]
        return max(near or [depths[e]])

    shift_high, shift_low = np.empty(x.size), np.empty(x.size)
    for i, xi in enumerate(x):
        d = depth_at(float(xi))
        window = (grid >= xi - d - 1e-9) & (grid <= xi + d + 1e-9)
        shift_high[i], shift_low[i] = high[window].max(), low[window].min()
    return shift_high, shift_low


#: The bars a drawing runs past the point they stop being needed, here EHE-08 §69.5.1.2's least
#: anchorage in position II, f_yk/14 · Ø (MPa, the length in Ø): what the elevation shows, not a
#: detailing. The full anchorage and laps are not drawn.
def anchorage(data: dict[str, Any], diameter: float) -> float:
    """l_bII,min of a bar named as a layout names it, in the span's unit."""
    units = common.units_of(data)
    f_y = float((_number(data.get("fy"), "fy") * ureg(units.f_y)).to("MPa").magnitude)
    length = f_y / 14 * units.bar(diameter)
    return float(length.to(LENGTH[units.name]).magnitude)


def _groups(layout: dict[str, Any], keep: tuple[int, ...]) -> dict[str, Any]:
    """The groups of a face layout, renumbered from 1: n1 (the corners) or the rest."""
    found: dict[str, Any] = {}
    for slot, index in enumerate((i for i in keep if layout.get(f"n{i}")), start=1):
        found[f"n{slot}"] = layout[f"n{index}"]
        found[f"d{slot}"] = layout[f"d{index}"]
    return found


def _area(layout: dict[str, Any], units: common.Units) -> float:
    return round(sum(layout[f"n{i}"] * units.bar_area(layout[f"d{i}"]) for i in range(1, 5) if layout.get(f"n{i}")), 3)


def _reach(x: list[float], need: list[float], base: float, from_end: bool) -> float | None:
    """How far from one end of the span the face needs more than its base: the station where the
    requirement drops to the base, interpolated, or None when the end itself needs no more."""
    xs, values = (x[::-1], need[::-1]) if from_end else (x, need)
    if values[0] <= base + 1e-9:
        return None
    for i in range(1, len(xs)):
        if values[i] <= base + 1e-9:
            share = (values[i - 1] - base) / (values[i - 1] - values[i])
            return xs[i - 1] + share * (xs[i] - xs[i - 1])
    return xs[-1]


def _bars(data: dict[str, Any], study: Analysis, e: int, design: dict[str, Any]) -> dict[str, Any]:
    """The bars of a span as they are placed: a base along the whole span on each face and the
    additions only where the shifted moment needs more, each run on l_bII,min past that point.
    The base of the bottom is its two corner bars (n1); the top's is two mounting bars, Ø8 (#3),
    along the whole beam, and over each support in hogging the design's top steel is added to them,
    at least as far as the top is in tension when the mounting bars alone are under the minimum.
    The provided area along the span follows."""
    units = common.units_of(data)
    start, end = study.faces[e]
    profile = design["profile"]
    x = profile["x"]
    mounting = 8 if not units.us else 3
    found: dict[str, Any] = {}
    provided: dict[str, list[float]] = {}
    for face in ("bot", "top"):
        layout = design["layouts"].get(face) or {}
        if face == "top":
            # two mounting bars hold the stirrups where no top steel is needed; over the supports
            # the design's top steel is added to them
            base, extra = {"n1": 2, "d1": mounting}, _groups(layout, (1, 2, 3, 4))
        else:
            base, extra = _groups(layout, (1,)), _groups(layout, (2, 3, 4))
        need = profile[f"As_{face}_req"]
        base_area, extra_area = _area(base, units), _area(extra, units)
        found[f"{face}_base"] = {"bars": beam._layout_bars(base, units), "As": base_area}
        reaches = []
        if extra:
            run = anchorage(data, max(extra[f"d{i}"] for i in range(1, 5) if extra.get(f"n{i}")))
            if face == "bot":
                left, right = _reach(x, need, base_area, False), _reach(x, need, base_area, True)
                if left is not None or right is not None or max(need) > base_area + 1e-9:
                    over = [xi for xi, value in zip(x, need, strict=True) if value > base_area + 1e-9]
                    if over:
                        reaches.append((max(min(over) - run, start), min(max(over) + run, end), "span"))
            else:
                # where the top is in tension it carries at least its minimum: when the mounting bars
                # alone fall short of it, the addition runs as far as the top is in tension at all
                short = profile.get("As_top_min", 0.0) > base_area + 1e-9
                threshold = 0.0 if short else base_area
                left, right = _reach(x, need, threshold, False), _reach(x, need, threshold, True)
                if left is not None:
                    reaches.append((start, min(left + run, end), "left"))
                if right is not None:
                    reaches.append((max(right - run, start), end, "right"))
                # two additions that meet, or would leave less than an anchorage between them, are
                # one bar over the whole span
                if len(reaches) == 2 and reaches[1][0] - reaches[0][1] < run:
                    reaches = [(start, end, "both")]
        found[f"{face}_add"] = [
            {"bars": beam._layout_bars(extra, units), "As": extra_area, "x0": round(a, 4), "x1": round(b, 4), "at": at}
            for a, b, at in reaches
        ]
        provided[face] = [
            round(base_area + sum(extra_area for a, b, _ in reaches if a - 1e-9 <= xi <= b + 1e-9), 3) for xi in x
        ]
    profile["As_bot_placed"], profile["As_top_placed"] = provided["bot"], provided["top"]
    return found


def _designs(data: dict[str, Any], study: Analysis) -> list[dict[str, Any]]:
    units = common.units_of(data)
    words = WORDS.get(data.get("lang"), WORDS["en"])
    depths = [effective_depth(data, element) for element in study.elements]
    found = []
    for e, element in enumerate(study.elements):
        demands = _demands(study, e, depths[e])
        # The design reads the forces, not where they come from: sorted and named in order, the end
        # span of a symmetrical beam and its mirror are one design, from the cache.
        values = sorted(
            (round(M, 3), round(V, 3)) for M, V in demands.values() if abs(M) > _NOTHING or V > _NOTHING
        ) or [(0.01, 0.0)]  # an unloaded element: designed for its minimum under a token moment
        rows = [{"label": f"C{i + 1}", "M_y": M, "V_z": V, "N_x": 0} for i, (M, V) in enumerate(values)]
        section = _element_data(data, element, study.ratio) | {"forces": rows}
        design = dict(_design(json.dumps(section, sort_keys=True)))
        # each critical section against the face that resists it: the bottom in the span, the top
        # at each support (one top layout for both, each with its own demand)
        design["demands"] = {
            key: {
                "M": round(M, 2),
                "V": round(V, 2),
                "label": words[key],
                "DCR": round(abs(M) / design["capacity"]["bottom" if key == "M+" else "top"], 3)
                if design["capacity"]["bottom" if key == "M+" else "top"] > 0
                else None,
            }
            for key, (M, V) in demands.items()
        }
        design["units"] = units.name
        design["d"] = round(effective_depth(data, element), 4)
        # the steel along the span, required against provided
        start, end = study.faces[e]
        x = np.linspace(start, end, PROFILE_STATIONS)
        after = np.zeros(x.size, bool)
        after[0] = True
        bounds = analysis.envelope(study.design, x, after)
        shear = np.maximum(np.abs(bounds.V_max), np.abs(bounds.V_min))
        # the moment shifted by d: what the bars along the span have to carry
        high, low = shifted(study, depths, x)
        hogging = min([0.0] + [demands[key][0] for key in ("M-l", "M-r", "M-o") if key in demands])
        high, low = np.minimum(high, demands["M+"][0]), np.maximum(low, hogging)
        stations = [
            [round(max(float(a), 0.0), 3), round(min(float(b), 0.0), 3), round(float(v), 3)]
            for a, b, v in zip(high, low, shear, strict=True)
        ]
        payload = section | {"layouts": design["layouts"], "stations": stations}
        design["profile"] = {"x": _round(x)} | _profile(json.dumps(payload, sort_keys=True))
        design["zones"] = _zones(data, study, e, design)
        design["bars"] = _bars(data, study, e, design)
        found.append(design)
    return found


# ------------------------------------------------------------------------------ redistribution


def _en_coefficients(f_ck: float) -> tuple[float, float]:
    """k₁ and k₂ of EN 1992-1-1 §5.5(4), recommended values: (5.10a) up to C50/60 and (5.10b),
    k₃ and k₄, above it, with ε_cu2 of Table 3.1."""
    if f_ck <= 50:
        return 0.44, 1.25 * (0.6 + 0.0014 / 0.0035)
    epsilon_cu2 = (2.6 + 35 * ((90 - f_ck) / 100) ** 4) / 1000
    return 0.54, 1.25 * (0.6 + 0.0014 / epsilon_cu2)


def redistribution_limit(code: str, epsilon_t: float | None, x_u_d: float | None, f_ck: float = 30.0) -> float | None:
    """The most a code lets a support's moment be reduced, as a fraction, from how ductile the
    section over it is; ``None`` when mento did not say.

    - ACI 318-19 §6.6.5 and CIRSOC 201-25: none below εt = 0.0075; otherwise 1000·εt %, at most 20 %.
    - EN 1992-1-1 §5.5(4): δ ≥ k₁ + k₂·x_u/d and δ ≥ 0.7, so at most 1 − δ.
    """
    if code == "EN 1992-2004":
        if x_u_d is None:
            return None
        k_1, k_2 = _en_coefficients(f_ck)
        return max(0.0, 1.0 - max(k_1 + k_2 * x_u_d, EN_DELTA_MIN))
    if epsilon_t is None:
        return None
    if epsilon_t < ACI_EPSILON_MIN:
        return 0.0
    return min(1000 * epsilon_t, ACI_MAX_PERCENT) / 100


def _supports(data: dict[str, Any], study: Analysis, designs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Each support: its moment, elastic and as designed, and whether the redistribution asked of
    it is within what the code allows the sections that meet over it."""
    model = study.beam
    elastic = np.stack([analysis.support_moments(result) for result in study.ultimate]).min(0)
    designed = np.stack([analysis.support_moments(result) for result in study.design]).min(0)
    reactions = np.stack([result.reactions[:, 0] for result in study.design])
    service = np.stack([result.reactions[:, 0] for result in study.service])
    redistributable = set(model.redistributable)
    f_ck = float(data.get("fc") or 0)
    lengths = [e.length for e in study.elements]
    found = []
    for j in model.held:
        row: dict[str, Any] = {
            "name": _support_name(model.held.index(j)),
            "x": round(float(model.nodes[j]), 4),
            "kind": model.supports[j],
            "M_elastic": round(float(min(elastic[j], 0.0)), 2),
            "M_design": round(float(min(designed[j], 0.0)), 2),
            "R_max": round(float(reactions[:, j].max()), 2),
            "R_min": round(float(reactions[:, j].min()), 2),
            "R_service": round(float(service[:, j].max()), 2),
            "redistribution": None,
        }
        if study.ratio and j in redistributable and elastic[j] < -_NOTHING:
            row["redistribution"] = _check_redistribution(data, study, designs, j, f_ck, lengths)
        found.append(row)
    return found


def _check_redistribution(
    data: dict[str, Any],
    study: Analysis,
    designs: list[dict[str, Any]] | None,
    j: int,
    f_ck: float,
    lengths: list[float],
) -> dict[str, Any]:
    requested = round(100 * study.ratio, 2)
    check: dict[str, Any] = {"requested": requested, "max": None, "ok": None, "reason": None}
    if designs is None:
        return check
    adjacent = [e for e in (j - 1, j) if 0 <= e < len(study.elements)]
    limits = [redistribution_limit(data["code"], designs[e]["epsilon_t"], designs[e]["x_u_d"], f_ck) for e in adjacent]
    if any(limit is None for limit in limits):
        check["reason"] = "unknown"  # a mento that does not report εt or x_u/d
        check["ok"] = False
        return check
    limit = min(limits)
    if data["code"] == "EN 1992-2004" and len(adjacent) == 2:
        a, b = (lengths[e] for e in adjacent)
        if max(a, b) / min(a, b) > EN_SPAN_RATIO + 1e-9:
            limit, check["reason"] = 0.0, "spans"
    if limit == 0.0 and check["reason"] is None:
        check["reason"] = "ductility"
    check["max"] = round(100 * limit, 1)
    check["ok"] = requested <= 100 * limit + 1e-6
    return check


def _support_name(index: int) -> str:
    """A, B, …, Z, AA, …: the name of the support at ``index`` along the beam."""
    name, index = "", index + 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


# ------------------------------------------------------------------------------- the deflection


def _deflections(study: Analysis) -> list[dict[str, Any]]:
    """The largest deflection of each span and overhang under the service load, times the
    multiplier for cracking and creep, against its clear span (between the faces)/250, an overhang
    2·length/250. In the span's unit; the rows convert it to mm (in)."""
    x, after = analysis.stations(study.service)
    values = analysis.diagrams(study.service, x, after)[..., 3] * study.deflection_factor
    found = []
    for e, element in enumerate(study.elements):
        start, end = study.faces[e]
        inside = (x >= start) & (x <= end)
        down = float(-values[:, inside].min())
        up = float(values[:, inside].max())
        clear = end - start
        allowed = clear / (DEFLECTION_LIMIT / (2 if element.kind != "span" else 1))
        found.append({"element": e, "down": down, "up": up, "clear": clear, "allowed": allowed,
                      "ok": max(down, up) <= allowed + 1e-12})  # fmt: skip
    return found


# --------------------------------------------------------------------------------------- output


def _diagrams(study: Analysis, depths: list[float]) -> dict[str, Any]:
    """What the page draws, at the stations of every arrangement: the envelopes of the moment,
    as designed, shifted by d and elastic, rounded inside the columns, and of the shear, and the
    service deflection."""
    x, after = analysis.stations(study.design + study.ultimate + study.service, per_element=32)
    # the faces of the columns, where the design reads and the page writes its values
    faces = np.array([face for pair in study.faces for face in pair])
    sides = np.array([side for _ in study.faces for side in (True, False)])
    x, after = np.concatenate([x, faces]), np.concatenate([after, sides])
    order = np.lexsort((after, x))
    x, after = x[order], after[order]
    keep = np.concatenate([[True], (np.diff(x) > 1e-9) | (np.diff(after.astype(int)) != 0)])
    x, after = x[keep], after[keep]
    design = analysis.envelope(study.design, x, after)
    columns = _columns_of(study)
    moments = rounded(study.design, x, after, columns)
    elastic = rounded(study.ultimate, x, after, columns)
    deflection = analysis.diagrams(study.service, x, after)[..., 3]
    high, low = shifted(study, depths, x)
    return {
        "x": _round(x),
        "after": [bool(value) for value in after],
        "M_max": _round(moments.max(0), 3),
        "M_min": _round(moments.min(0), 3),
        "M_shift_max": _round(high, 3),
        "M_shift_min": _round(low, 3),
        "M_elastic_max": _round(elastic.max(0), 3),
        "M_elastic_min": _round(elastic.min(0), 3),
        "V_max": _round(design.V_max, 3),
        "V_min": _round(design.V_min, 3),
        "v_max": _round(deflection.max(0), 7),
        "v_min": _round(deflection.min(0), 7),
    }


def _outline(data: dict[str, Any], study: Analysis) -> dict[str, Any]:
    units = common.units_of(data)
    return {
        "ok": True,
        "version": mento.__version__,
        "code": data["code"],
        "units": units.name,
        "elements": [
            {
                "name": element.name,
                "kind": element.kind,
                "x0": round(float(study.beam.nodes[e]), 4),
                "x1": round(float(study.beam.nodes[e + 1]), 4),
                "faces": _round(study.faces[e]),
                "width": element.width,
                "height": element.height,
                "self_weight": round(study.self_weight[e], 3) if data.get("self_weight", True) else 0.0,
                "d": round(effective_depth(data, element), 4),
            }
            for e, element in enumerate(study.elements)
        ],
        "diagrams": _diagrams(study, [effective_depth(data, element) for element in study.elements]),
        "arrangements": len(study.design),
    }


def _deflection_rows(study: Analysis, units: common.Units) -> list[dict[str, Any]]:
    small = "mm" if not units.us else "inch"
    span = LENGTH[units.name]
    rows = []
    for row in _deflections(study):
        scale = float((1 * ureg(span)).to(small).magnitude)
        worst = max(row["down"], row["up"])
        rows.append(
            {
                "element": row["element"],
                "deflection": round(worst * scale, 2),
                "allowed": round(row["allowed"] * scale, 2),
                "ratio": round(row["clear"] / worst) if worst > 0 else None,
                "clear": round(row["clear"], 3),
                "factor": study.deflection_factor,
                "ok": row["ok"],
            }
        )
    return rows


def _run(data: dict[str, Any], design: bool) -> dict[str, Any]:
    lang = data.get("lang", "en")
    mento.set_language(lang if lang in mento.available_languages() else "en")
    if data.get("code") not in COMBINATIONS:
        raise common.InputError("code", "unknown")
    study = _analyse(data)
    units = common.units_of(data)
    result = _outline(data, study)
    designs = _designs(data, study) if design else None
    supports = _supports(data, study, designs)
    result["supports"] = supports
    result["deflections"] = _deflection_rows(study, units)
    if designs is None:
        return result
    redistributed = [row["redistribution"] for row in supports if row["redistribution"]]
    result["designs"] = designs
    result["complies"] = all(item["complies"] for item in designs) and all(check["ok"] for check in redistributed)
    result["dcr"] = max(
        max(item["flexure"]["bottom"]["DCR"], item["flexure"]["top"]["DCR"], item["shear"]["DCR"]) for item in designs
    )
    return result


def analyse(payload: str) -> str:
    """The analysis alone: diagrams, reactions and deflections, at once. Never raises."""
    return _answer(payload, design=False)


def run(payload: str) -> str:
    """Analysis and design. Never raises: errors come back as ``{"ok": false}``."""
    return _answer(payload, design=True)


def _answer(payload: str, design: bool) -> str:
    try:
        result = _run(json.loads(payload), design)
    except common.InputError as error:
        result = {"ok": False, "kind": "input", "field": error.field, "message": str(error)}
    except Exception as error:  # noqa: BLE001 - everything must reach the page as JSON
        result = {"ok": False, "kind": "mento", "message": f"{type(error).__name__}: {error}"}
    return json.dumps(result)
