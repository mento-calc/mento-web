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
from mento import Node, ureg

import beam

# The worked example every visitor opens: three spans that pass with the governing DCR between
# 0.7 and 0.9, and the redistribution it asks for within what the code allows.
EXAMPLE: dict[str, Any] = {
    "code": "CIRSOC 201-25",
    "units": "si",
    "lang": "es",
    "label": "V-101",
    "fc": 25,
    "fy": 420,
    "cover": 25,
    "support_width": 20,
    "spans": [
        {"L": 5.0, "width": 20, "height": 55},
        {"L": 6.0, "width": 20, "height": 55},
        {"L": 5.0, "width": 20, "height": 55},
    ],
    "ends": {"left": {"type": "pin"}, "right": {"type": "pin"}},
    "loads": [
        {"case": "D", "type": "uniform", "element": 0, "value": 12.0},
        {"case": "D", "type": "uniform", "element": 1, "value": 12.0},
        {"case": "D", "type": "uniform", "element": 2, "value": 12.0},
        {"case": "L", "type": "uniform", "element": 0, "value": 5.0},
        {"case": "L", "type": "uniform", "element": 1, "value": 5.0},
        {"case": "L", "type": "uniform", "element": 2, "value": 5.0},
    ],
    "self_weight": True,
    "redistribution": 15,
}
EXAMPLE_US: dict[str, Any] = {
    "code": "ACI 318-19",
    "units": "us",
    "lang": "en",
    "label": "B-101",
    "fc": 4000,
    "fy": 60,
    "cover": 1.5,
    "support_width": 12,
    "spans": [
        {"L": 16.0, "width": 12, "height": 22},
        {"L": 20.0, "width": 12, "height": 22},
        {"L": 16.0, "width": 12, "height": 22},
    ],
    "ends": {"left": {"type": "pin"}, "right": {"type": "pin"}},
    "loads": [
        {"case": "D", "type": "uniform", "element": 0, "value": 0.8},
        {"case": "D", "type": "uniform", "element": 1, "value": 0.8},
        {"case": "D", "type": "uniform", "element": 2, "value": 0.8},
        {"case": "L", "type": "uniform", "element": 0, "value": 0.6},
        {"case": "L", "type": "uniform", "element": 1, "value": 0.6},
        {"case": "L", "type": "uniform", "element": 2, "value": 0.6},
    ],
    "self_weight": True,
    "redistribution": 15,
}

#: The most spans a beam may have: with an overhang at each end the live load has 2¹⁰ arrangements.
MAX_SPANS = 8
#: Load factors of the ultimate combinations: (permanent, live). The live load is patterned span by
#: span on top of the permanent one; a combination without it is the arrangement with none on.
#: ACI 318-19 §5.3.1 and CIRSOC 201-25 the same: 1.4D, and 1.2D + 1.6L. EN 1990 eq. (6.10) with
#: EN 1992-1-1 §5.1.3: 1.35G throughout and 1.5Q on the spans the arrangement loads.
COMBINATIONS = {
    "ACI 318-19": [("1.4D", 1.4, 0.0), ("1.2D+1.6L", 1.2, 1.6)],
    "CIRSOC 201-25": [("1.4D", 1.4, 0.0), ("1.2D+1.6L", 1.2, 1.6)],
    "EN 1992-2004": [("1.35G+1.5Q", 1.35, 1.5)],
}
#: The deflection is checked under the service load, D + L, the live load patterned: span/250 and
#: an overhang 2·length/250, on the gross section. It is shown, never part of the verdict.
DEFLECTION_LIMIT = 250.0
#: ACI 318-19 §6.6.5.1 and CIRSOC 201-25: no redistribution below εt = 0.0075, at most 1000·εt %,
#: never past 20 %.
ACI_EPSILON_MIN, ACI_MAX_PERCENT = 0.0075, 20.0
#: EN 1992-1-1 §5.5(4): δ ≥ k₁ + k₂·x_u/d and δ ≥ k₅ = 0.7 (Class B and C reinforcement), with
#: adjacent spans between 0.5 and 2 times each other.
EN_DELTA_MIN, EN_SPAN_RATIO = 0.70, 2.0
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

    left, right = ends.get("left") or {"type": "pin"}, ends.get("right") or {"type": "pin"}
    for end in (left, right):
        if end.get("type") not in ("pin", "fixed", "overhang"):
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
    """The loads by case: ``D`` on the whole beam, and the live load of each element in a case
    of its own, ``L@<element>``, so the arrangements can switch it on and off span by span."""
    cases: dict[str, list[analysis.Load]] = {"D": []}
    if data.get("self_weight"):
        cases["D"] += [analysis.Uniform(e, w) for e, w in enumerate(weight)]
    for row in data.get("loads") or []:
        case = row.get("case")
        if case not in ("D", "L"):
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
        cases.setdefault("D" if case == "D" else f"L@{e}", []).append(load)
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
    try:
        model = analysis.Beam(tuple(e.length for e in elements), tuple(stiffness), supports)
        solution = analysis.solve(model, _loads(data, elements, weight))
    except analysis.AnalysisError as error:
        raise common.InputError(error.field if error.field != "lengths" else "spans", str(error)) from None

    ratio = _number(data.get("redistribution") or 0, "redistribution", positive=False) / 100
    if ratio >= 0.3 + 1e-9:  # no code goes past 30 %: EN's δ ≥ 0.7
        raise common.InputError("redistribution", "max")
    live = {case: 1.0 for case in solution.cases if case.startswith("L@")}
    ultimate, labels = [], []
    for label, dead, factor in COMBINATIONS[data["code"]]:
        patterned = {case: factor for case in live} if factor else {}
        found = analysis.arrangements(solution, {"D": dead}, patterned)
        ultimate += [arrangement.result for arrangement in found]
        labels += [label] * len(found)
    design = [analysis.redistribute(result, ratio) if ratio else result for result in ultimate]
    service = [arrangement.result for arrangement in analysis.arrangements(solution, {"D": 1.0}, live)]

    width = _number(data.get("support_width") or 0, "support_width", positive=False) * section
    half = float((width / 2).to(span).magnitude)
    faces = []
    for e, element in enumerate(elements):
        x0, x1 = model.nodes[e], model.nodes[e + 1]
        start = x0 + (half if supports[e] != "free" else 0.0)
        end = x1 - (half if supports[e + 1] != "free" else 0.0)
        if end - start <= 0.1 * element.length:
            raise common.InputError("support_width", "too_wide")
        faces.append((float(start), float(end)))
    return Analysis(elements, model, solution, ultimate, design, service, labels, ratio, faces)


def _at(results: list[analysis.Result], x: float, after: bool) -> np.ndarray:
    """V and M of every result at one section, shape (results, 2)."""
    return analysis.diagrams(results, np.array([x]), np.array([after]))[:, 0, :2]


def _round(values: Any, digits: int = 4) -> list[float]:
    return [round(float(value), digits) for value in np.asarray(values).ravel()]


def _demands(study: Analysis, e: int) -> dict[str, tuple[float, float]]:
    """The critical sections of an element, as (M, |V|) pairs: the largest sagging moment along
    it, and at each face of a support its largest hogging moment with the largest shear there.
    The shear is read at the face, a little on the safe side of the code's d from it."""
    x, after = analysis.stations(study.design)
    start, end = study.faces[e]
    inside = (x >= start) & (x <= end)
    bounds = analysis.envelope(study.design, x[inside], after[inside])
    demands: dict[str, tuple[float, float]] = {"M+": (max(float(bounds.M_max.max()), 0.0), 0.0)}
    kinds = study.beam.supports
    for key, at, past, node in (("M-l", start, True, e), ("M-r", end, False, e + 1)):
        if kinds[node] == "free":
            continue
        values = _at(study.design, at, past)
        demands[key] = (min(float(values[:, 1].min()), 0.0), float(np.abs(values[:, 0]).max()))
    if study.elements[e].kind != "span":  # an overhang: one support, its moment is the support's
        demands["M-o"] = demands.pop("M-r" if study.elements[e].kind == "left" else "M-l")
    return demands


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
    options = beam._options(data, designed)
    selected, _ = beam._select(options, {})
    layouts = {group: beam._selected_layout(options, group, selected) for group in options}
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        checked, node = beam._checked(data, forces, layouts)
    flexure, shear = checked.flexure_design, checked.shear_design
    complies = bool(flexure.complies) and shear.DCR <= 1
    return {
        "rebar": {group: beam._layout_bars(layout, units) for group, layout in layouts.items()},
        "rows": {group: beam._layout_rows(layout, units) for group, layout in layouts.items()},
        "layouts": layouts,
        "flexure": {"bottom": beam._face(flexure.bottom, units), "top": beam._face(flexure.top, units)},
        "shear": {"stirrups": beam._layout_bars(layouts.get("st") or {}, units), "DCR": round(float(shear.DCR), 3)},
        "complies": complies,
        # how ductile the top face is, which the redistribution over a support reads: None with a
        # mento that does not report it yet (before 1.7.0)
        "epsilon_t": getattr(flexure.top, "epsilon_t", None),
        "x_u_d": getattr(flexure.top, "x_u_d", None),
        "notices": common.warning_notices(node, list(captured)),
        "section": beam._section(checked, layouts, units),
    }


def _designs(data: dict[str, Any], study: Analysis) -> list[dict[str, Any]]:
    units = common.units_of(data)
    words = WORDS.get(data.get("lang"), WORDS["en"])
    found = []
    for e, element in enumerate(study.elements):
        demands = _demands(study, e)
        # The design reads the forces, not where they come from: sorted and named in order, the end
        # span of a symmetrical beam and its mirror are one design, from the cache.
        values = sorted(
            (round(M, 3), round(V, 3)) for M, V in demands.values() if abs(M) > _NOTHING or V > _NOTHING
        ) or [(0.01, 0.0)]  # an unloaded element: designed for its minimum under a token moment
        rows = [{"label": f"C{i + 1}", "M_y": M, "V_z": V, "N_x": 0} for i, (M, V) in enumerate(values)]
        section = _element_data(data, element, study.ratio) | {"forces": rows}
        design = dict(_design(json.dumps(section, sort_keys=True)))
        design["demands"] = {
            key: {"M": round(M, 2), "V": round(V, 2), "label": words[key]} for key, (M, V) in demands.items()
        }
        design["units"] = units.name
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
    """The largest deflection of each span and overhang under the service arrangements, against
    span/250 (an overhang 2·length/250). In the page's length unit, the deflection in mm (in)."""
    x, after = analysis.stations(study.service)
    values = analysis.diagrams(study.service, x, after)[..., 3]
    nodes = study.beam.nodes
    found = []
    for e, element in enumerate(study.elements):
        inside = (x >= nodes[e]) & (x <= nodes[e + 1])
        down = float(-values[:, inside].min())
        up = float(values[:, inside].max())
        allowed = element.length / (DEFLECTION_LIMIT / (2 if element.kind != "span" else 1))
        found.append({"element": e, "down": down, "up": up, "allowed": allowed, "ok": max(down, up) <= allowed + 1e-12})
    return found


# --------------------------------------------------------------------------------------- output


def _diagrams(study: Analysis) -> dict[str, Any]:
    """What the page draws, at the stations of every arrangement: the envelopes of the moment,
    as designed and elastic, and of the shear, and the service deflection."""
    x, after = analysis.stations(study.design + study.ultimate + study.service, per_element=32)
    design = analysis.envelope(study.design, x, after)
    elastic = analysis.envelope(study.ultimate, x, after)
    deflection = analysis.diagrams(study.service, x, after)[..., 3]
    return {
        "x": _round(x),
        "after": [bool(value) for value in after],
        "M_max": _round(design.M_max, 3),
        "M_min": _round(design.M_min, 3),
        "M_elastic_max": _round(elastic.M_max, 3),
        "M_elastic_min": _round(elastic.M_min, 3),
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
            }
            for e, element in enumerate(study.elements)
        ],
        "diagrams": _diagrams(study),
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
                "ratio": round(study.elements[row["element"]].length / worst) if worst > 0 else None,
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
