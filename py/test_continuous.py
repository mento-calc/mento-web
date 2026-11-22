"""The continuous beam glue: the example, what mento receives, the redistribution limits, errors.

The solver itself is tested in ``test_analysis.py`` and ``py/validation``; here, what the page
sends becomes the right beam, the right combinations reach mento at the right sections, and the
redistribution is held to each code's limit. The checks that read how ductile a section is
(``epsilon_t``, ``x_u_d``) need mento 1.7.0 and are skipped with an older one.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import analysis
import common
import numpy as np
import pytest
from common import ureg
from mento.design_results import FlexureFaceDesign

import continuous

#: mento reports how ductile a face is from 1.7.0: before it, the redistribution cannot be checked.
DUCTILITY = "epsilon_t" in getattr(FlexureFaceDesign, "__dataclass_fields__", {})
needs_ductility = pytest.mark.skipif(not DUCTILITY, reason="mento reports epsilon_t and x_u_d from 1.7.0")

L, q = 5.0, 20.0  # each span, kN/m


def _run(data: dict[str, Any], design: bool = True) -> dict[str, Any]:
    function = continuous.run if design else continuous.analyse
    return json.loads(function(json.dumps(data)))


def _two_spans(**changes: Any) -> dict[str, Any]:
    """Two spans of L under q of dead load only, no self weight, supports with no width."""
    data: dict[str, Any] = {
        "code": "CIRSOC 201-25",
        "units": "si",
        "lang": "es",
        "fc": 25,
        "fy": 420,
        "cover": 25,
        "support_width": 0,
        "spans": [{"L": L, "width": 20, "height": 50}, {"L": L, "width": 20, "height": 50}],
        "ends": {"left": {"type": "pin"}, "right": {"type": "pin"}},
        "loads": [{"case": "D", "type": "uniform", "element": e, "value": q} for e in (0, 1)],
        "self_weight": False,
        "redistribution": 0,
    }
    return data | changes


# --------------------------------------------------------------------------------- the examples


@needs_ductility
@pytest.mark.parametrize("example", ["EXAMPLE", "EXAMPLE_US"])
def test_the_examples_pass_within_the_rule_of_the_site(example: str) -> None:
    """Every example the site opens passes, its governing DCR between 0.7 and 0.99, and the
    redistribution it asks for is within what the code allows over each interior support."""
    result = _run(getattr(continuous, example))
    assert result["ok"] and result["complies"]
    assert 0.7 <= result["dcr"] <= 0.99, result["dcr"]
    checks = [row["redistribution"] for row in result["supports"] if row["redistribution"]]
    assert all(check["ok"] and check["requested"] <= check["max"] for check in checks)


def test_the_analysis_alone_answers_without_designing() -> None:
    result = _run(continuous.EXAMPLE, design=False)
    assert result["ok"] and "designs" not in result
    assert result["arrangements"] == 4  # the four combinations of EHE-08 (EN 1992), live load everywhere
    patterned = _run(continuous.EXAMPLE | {"pattern": True}, design=False)
    assert patterned["arrangements"] == 2 + 2 * 2**3  # those without it once, those with it on every subset
    assert len(result["diagrams"]["x"]) == len(result["diagrams"]["M_max"]) == len(result["diagrams"]["v_min"])


# ---------------------------------------------------------------------- what reaches mento


def test_two_spans_reach_mento_with_their_closed_forms() -> None:
    """Dead load alone, so 1.4D governs: −1.4·qL²/8 over the middle support and 1.4·9qL²/128 in
    each span; the shear at d from the support, 1.4·(5qL/8 − q·d), d = h − cc − 8 mm − 8 mm."""
    result = _run(_two_spans())
    first = result["designs"][0]["demands"]
    d = 0.50 - 0.025 - 0.016
    assert result["designs"][0]["d"] == pytest.approx(d)
    assert first["M+"]["M"] == pytest.approx(1.4 * 9 * q * L**2 / 128, abs=0.01)
    assert first["M-r"]["M"] == pytest.approx(-1.4 * q * L**2 / 8, abs=0.01)
    assert first["M-r"]["V"] == pytest.approx(1.4 * (5 * q * L / 8 - q * d), abs=0.01)
    assert first["M-l"]["M"] == pytest.approx(0.0, abs=0.01)
    middle = result["supports"][1]
    assert middle["M_elastic"] == middle["M_design"] == pytest.approx(-1.4 * q * L**2 / 8, abs=0.01)
    assert middle["R_max"] == pytest.approx(1.4 * 10 * q * L / 8, abs=0.01)


def test_the_moment_is_read_at_the_face_of_the_support() -> None:
    """With a support 40 cm wide the hogging moment is read 20 cm from its axis: less than over it."""
    narrow, wide = _run(_two_spans()), _run(_two_spans(support_width=40))
    axis, face = narrow["designs"][0]["demands"]["M-r"]["M"], wide["designs"][0]["demands"]["M-r"]["M"]
    w = 1.4 * q
    expected = -w * L**2 / 8 + (5 * w * L / 8) * 0.2 - w * 0.2**2 / 2  # M at the axis, back along the shear
    assert face == pytest.approx(expected, abs=0.01) and abs(face) < abs(axis)


def test_the_live_load_is_patterned_span_by_span() -> None:
    """Three spans, live load alone factored 1.6: the sagging moment of the first span is the
    largest of the arrangements, the first and third spans loaded, not the whole beam."""
    data = _two_spans(spans=[{"L": L, "width": 20, "height": 50}] * 3, pattern=True)
    data["loads"] = [{"case": "L", "type": "uniform", "element": e, "value": q} for e in range(3)]
    demand = _run(data)["designs"][0]["demands"]["M+"]["M"]
    beam = analysis.Beam((L,) * 3, (1.0,) * 3, ("pin",) * 4)
    on_one_and_three = analysis.solve(beam, {"L": [analysis.Uniform(0, 1.6 * q), analysis.Uniform(2, 1.6 * q)]})["L"]
    everywhere = analysis.solve(beam, {"L": [analysis.Uniform(e, 1.6 * q) for e in range(3)]})["L"]
    x = np.linspace(0, L, 2001)
    assert demand == pytest.approx(on_one_and_three.moment(x).max(), abs=0.01)
    assert demand > everywhere.moment(x).max() + 1.0


def test_the_self_weight_joins_the_dead_load() -> None:
    """20 × 50 cm at 25 kN/m³ (mento's 2500 kg/m³ times g): 2.45 kN/m more on every span."""
    bare, heavy = _run(_two_spans(), design=False), _run(_two_spans(self_weight=True), design=False)
    weight = 0.2 * 0.5 * 2500 * 9.80665 / 1000
    extra = heavy["supports"][1]["R_max"] - bare["supports"][1]["R_max"]
    assert extra == pytest.approx(1.4 * 10 * weight * L / 8, abs=0.01)


def test_the_service_deflection_of_one_span() -> None:
    """One simple span under D + L: 5·w·L⁴/(384·E·I), with mento's E_c (ACI 318-19 §19.2.2.1(a),
    0.043·w_c^1.5·√f'c, from its 2500 kg/m³) and the gross section."""
    data = _two_spans(spans=[{"L": 6.0, "width": 20, "height": 50}])
    data["loads"] = [
        {"case": "D", "type": "uniform", "element": 0, "value": 10.0},
        {"case": "L", "type": "uniform", "element": 0, "value": 5.0},
    ]
    row = _run(data, design=False)["deflections"][0]
    E = 2500**1.5 * 0.043 * 25**0.5 * 1e3  # kN/m², 26 875 MPa
    EI = E * 0.2 * 0.5**3 / 12
    instant = 5 * 15.0 * 6.0**4 / (384 * EI) * 1e3
    assert row["deflection"] == pytest.approx(3 * instant, abs=0.01)  # times 3 for cracking and creep
    assert row["allowed"] == pytest.approx(6000 / 250) and row["ok"]
    once = _run(data | {"deflection_factor": 1}, design=False)["deflections"][0]
    assert once["deflection"] == pytest.approx(instant, abs=0.01)


# ---------------------------------------------------------------------------- redistribution


def test_redistribution_lowers_the_support_and_raises_the_span() -> None:
    """20 % of −1.4·qL²/8 over the middle support; the span peaks at R_A²/(2·1.4q)."""
    result = _run(_two_spans(redistribution=20), design=False)
    w = 1.4 * q
    assert result["supports"][1]["M_design"] == pytest.approx(-0.8 * w * L**2 / 8, abs=0.01)
    assert result["supports"][1]["M_elastic"] == pytest.approx(-w * L**2 / 8, abs=0.01)
    reaction = w * L / 2 - 0.8 * w * L / 8
    assert max(result["diagrams"]["M_max"]) == pytest.approx(reaction**2 / (2 * w), abs=0.01)


@pytest.mark.parametrize(
    ("code", "epsilon_t", "x_u_d", "expected"),
    [
        ("ACI 318-19", 0.0075, None, 0.075),
        ("CIRSOC 201-25", 0.0150, None, 0.15),
        ("ACI 318-19", 0.0200, None, 0.20),
        ("ACI 318-19", 0.0350, None, 0.20),
        ("ACI 318-19", 0.0074, None, 0.0),
        ("EN 1992-2004", None, 0.208, 0.30),
        ("EN 1992-2004", None, 0.328, 0.15),
        ("EN 1992-2004", None, 0.448, 0.0),
        ("EN 1992-2004", None, 0.100, 0.30),  # never below δ = 0.7
        ("ACI 318-19", None, None, None),
    ],
)
def test_the_limit_of_each_code(
    code: str, epsilon_t: float | None, x_u_d: float | None, expected: float | None
) -> None:
    """ACI 318-19 §6.6.5 and CIRSOC 201-25: 1000·εt %, at most 20 %, none below εt = 0.0075.
    EN 1992-1-1 §5.5(4) up to C50/60: δ ≥ 0.44 + 1.25·x_u/d and δ ≥ 0.7."""
    found = continuous.redistribution_limit(code, epsilon_t, x_u_d, f_ck=30.0)
    assert found == (None if expected is None else pytest.approx(expected, abs=1e-3))


def test_the_coefficients_above_c50() -> None:
    """Eq. (5.10b) at C70/85: ε_cu2 = 2.6 + 35·0.2⁴ = 2.656 ‰, k₄ = 1.25·(0.6 + 1.4/2.656)."""
    k_3, k_4 = continuous._en_coefficients(70.0)
    assert k_3 == 0.54 and k_4 == pytest.approx(1.25 * (0.6 + 1.4 / 2.656), rel=1e-4)


@needs_ductility
def test_more_than_the_code_allows_does_not_pass() -> None:
    """ACI and CIRSOC stop at 20 %: 25 % is not cut back, the supports say they fail."""
    result = _run(continuous.EXAMPLE | {"code": "CIRSOC 201-25", "fy": 420, "redistribution": 25})
    checks = [row["redistribution"] for row in result["supports"] if row["redistribution"]]
    assert checks and not any(check["ok"] for check in checks)
    assert all(check["requested"] == 25 and check["max"] <= 20 for check in checks)
    assert not result["complies"]


@needs_ductility
def test_en_reads_the_neutral_axis_and_the_ratio_of_spans() -> None:
    """Under EN the beam is designed for δ = 1 − r and the limit read off x_u/d; spans more than
    twice each other refuse any redistribution (§5.5(4))."""
    data = copy.deepcopy(continuous.EXAMPLE) | {"code": "EN 1992-2004", "fy": 500, "redistribution": 20}
    checks = [row["redistribution"] for row in _run(data)["supports"] if row["redistribution"]]
    assert all(check["ok"] and check["max"] >= 20 for check in checks)
    data["spans"][1]["L"] = 11.0
    # the rule on adjacent spans is the inner supports'; an end held by its column keeps its limit
    inner = [row["redistribution"] for row in _run(data)["supports"] if row["name"] in ("B", "C")]
    assert all(not check["ok"] and check["reason"] == "spans" and check["max"] == 0 for check in inner)


@needs_ductility
def test_a_section_that_cannot_keep_its_delta_says_so() -> None:
    """A shallow, wide EN beam asked for 30 % (δ = 0.7): the fewest bars that fit the stirrups
    already put x_u/d past (δ − k₁)/k₂. mento's design warns on that face, the page shows it, and
    the support fails with the most the section allows, 1 − (0.44 + 1.25·x_u/d)."""
    data = _two_spans(code="EN 1992-2004", fc=20, fy=500, self_weight=True, redistribution=30)
    data["spans"] = [{"L": 3.0, "width": 30, "height": 20}] * 2
    data["loads"] = [{"case": "D", "type": "uniform", "element": e, "value": 4.0} for e in (0, 1)]
    result = _run(data)
    design = result["designs"][0]
    assert "x_u_above_redistribution_limit" in [notice["code"] for notice in design["notices"]]
    check = result["supports"][1]["redistribution"]
    assert check["requested"] == 30 and not check["ok"]
    assert check["max"] == pytest.approx(100 * (1 - (0.44 + 1.25 * design["x_u_d"])), abs=0.06)
    assert not result["complies"]


def test_an_overhang_is_designed_for_its_support_and_never_redistributed() -> None:
    data = copy.deepcopy(continuous.EXAMPLE) | {"redistribution": 0}
    data["ends"]["right"] = {"type": "overhang", "L": 1.5, "width": 30, "height": 50}
    data["loads"].append({"case": "D", "type": "point", "element": 3, "value": 10.0, "a": 1.5})
    result = _run(data)
    overhang = result["designs"][-1]["demands"]
    w = 1.35 * 0.3 * 0.5 * 2500 * 9.80665 / 1000  # its own weight: the example loads only the spans
    support = 1.5 - 0.15  # its face, half of the 30 cm column from the axis
    assert set(overhang) == {"M+", "M-o"}
    # the face's own statics, and over the column the peak rounded inside it, a little more
    face = -(w * support**2 / 2 + 1.35 * 10.0 * support)
    d, elements = result["diagrams"], result["elements"]
    a, b = elements[-2]["faces"][1], elements[-1]["faces"][0]
    peak = min(m for x, m in zip(d["x"], d["M_min"], strict=True) if a - 1e-9 <= x <= b + 1e-9)
    assert overhang["M-o"]["M"] == pytest.approx(peak, abs=0.02)
    assert face - 1.0 < overhang["M-o"]["M"] < face
    assert result["elements"][-1]["kind"] == "right" and result["supports"][-1]["kind"] == "pin"
    redistributed = _run(data | {"redistribution": 10}, design=False)["supports"]
    assert redistributed[-1]["M_design"] == redistributed[-1]["M_elastic"]


def test_a_fixed_end_is_a_support_that_hogs() -> None:
    data = _two_spans(ends={"left": {"type": "fixed"}, "right": {"type": "pin"}})
    result = _run(data | {"redistribution": 10}, design=False)
    assert result["supports"][0]["kind"] == "fixed" and result["supports"][0]["M_elastic"] < 0
    assert result["supports"][0]["M_design"] == pytest.approx(0.9 * result["supports"][0]["M_elastic"], abs=0.01)


# --------------------------------------------------------------------------------- the inputs


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"spans": []}, "spans"),
        ({"spans": [{"L": 5, "width": 20, "height": 50}] * 9}, "spans"),
        ({"spans": [{"L": 0, "width": 20, "height": 50}]}, "spans"),
        ({"ends": {"left": {"type": "hinge"}}}, "ends"),
        ({"ends": {"left": {"type": "overhang"}}}, "ends"),
        ({"loads": [{"case": "W", "type": "uniform", "element": 0, "value": 5}]}, "loads"),
        ({"loads": [{"case": "D", "type": "uniform", "element": 4, "value": 5}]}, "loads"),
        ({"loads": [{"case": "D", "type": "point", "element": 0, "value": 5, "a": 7}]}, "loads"),
        ({"loads": [{"case": "D", "type": "uniform", "element": 0, "value": 5, "a": 3, "b": 2}]}, "loads"),
        ({"loads": [{"case": "D", "type": "wind", "element": 0, "value": 5}]}, "loads"),
        ({"redistribution": 35}, "redistribution"),
        ({"redistribution": -5}, "redistribution"),
        ({"support_width": 500}, "support_width"),
        ({"code": "BS 8110"}, "code"),
        ({"fc": 0}, "fc"),
    ],
)
def test_each_input_error_names_its_field(changes: dict[str, Any], field: str) -> None:
    result = _run(_two_spans(**changes), design=False)
    assert not result["ok"] and result["kind"] == "input" and result["field"] == field


def test_run_never_raises() -> None:
    assert json.loads(continuous.run("not json"))["ok"] is False
    assert json.loads(continuous.run(json.dumps({"code": "CIRSOC 201-25"})))["ok"] is False


def test_the_names_follow_the_language() -> None:
    data = copy.deepcopy(continuous.EXAMPLE)
    data["ends"]["left"] = {"type": "overhang", "L": 1.0, "width": 20, "height": 55}
    names = [element["name"] for element in _run(data | {"lang": "en"}, design=False)["elements"]]
    assert names == ["Left overhang", "Span 1", "Span 2", "Span 3"]
    names = [element["name"] for element in _run(data, design=False)["elements"]]
    assert names == ["Voladizo izq.", "Vano 1", "Vano 2", "Vano 3"]


# ------------------------------------------------------------------------------------ columns


def _columns(widths: list[float], below: float = 3.0, above: float = 0.0, **extra: Any) -> list[dict[str, Any]]:
    return [{"width": w, "depth": 30, "H_below": below, "H_above": above} | extra for w in widths]


def test_a_column_holds_the_end_of_the_beam() -> None:
    """Each column holds its joint through a rigid arm a of half the beam's depth, over its clear
    height L: 4EI/L·(1 + 3a/L + 3a²/L²) to a fixed foundation, 3EI/L·(1 + a/L)² to a pinned one,
    6EI/L·(1 + 2a/L)² to another floor turning with this one. Under a roof the last storey's
    column holds α of that; above a floor, the last storey's is α bending with the roof and the
    rest pinned at its head, an intermediate one bending with the next floor."""
    data = _two_spans(spans=[{"L": L, "width": 30, "height": 50}], columns=_columns([30, 30]), head_fixity=0.3)
    data["ends"] = {"left": {"type": "column"}, "right": {"type": "column"}}
    data["loads"] = data["loads"][:1]  # one span, one load
    elements, supports = continuous._elements(data)
    concrete, _ = common.materials(data)
    EI = float((concrete.E_c * 0.3 * ureg.m * (0.3 * ureg.m) ** 3 / 12).to("kN*m**2").magnitude)
    clear, r = 3.0 - 0.5, 0.25 / (3.0 - 0.5)
    far = {
        "fixed": 4 * EI / clear * (1 + 3 * r + 3 * r**2),
        "pinned": 3 * EI / clear * (1 + r) ** 2,
        "floor": 6 * EI / clear * (1 + 2 * r) ** 2,
    }
    for base, k in far.items():
        _, springs, joints = continuous._columns(data | {"base": base}, supports, elements, concrete.E_c)
        assert springs == pytest.approx([0.3 * k, 0.3 * k]) and joints == [True, True]
        storey = data | {"base": base, "columns": _columns([30, 30], above=3.0)}
        _, springs, _ = continuous._columns(storey, supports, elements, concrete.E_c)
        assert springs[0] == pytest.approx(k + 0.3 * far["floor"] + 0.7 * far["pinned"])
        _, springs, _ = continuous._columns(storey | {"above": "intermediate"}, supports, elements, concrete.E_c)
        assert springs[0] == pytest.approx(k + far["floor"])

    pinned = _run(data | {"columns": _columns([30, 30], pinned=True)}, design=False)
    assert pinned["supports"][0]["M_elastic"] == pytest.approx(0.0, abs=0.01)
    roof = _run(data, design=False)["supports"][0]["M_elastic"]
    last = _run(data | {"columns": _columns([30, 30], above=3.0)}, design=False)["supports"][0]["M_elastic"]
    middle = _run(data | {"columns": _columns([30, 30], above=3.0), "above": "intermediate"}, design=False)
    assert middle["supports"][0]["M_elastic"] < last < roof - 1  # held from above, more when not the last


def test_the_moment_is_rounded_inside_each_column() -> None:
    """Inside a column the moment is the cubic tangent to the diagram at both faces, as CYPECAD
    draws it: it meets each face's moment and shear, and its peak lies between the faces."""
    study = continuous._analyse(continuous.EXAMPLE)
    a, b = study.faces[0][1], study.faces[1][0]
    columns = continuous._columns_of(study)
    assert columns[1] == pytest.approx((a, b))
    h = 1e-5
    x = np.array([a, a + h, b - h, b])
    after = np.array([False, False, False, True])
    rounded = continuous.rounded(study.design, x, after, columns)[0]
    plain = analysis.diagrams(study.design, x, after)[0]
    assert rounded[0] == pytest.approx(plain[0, 1]) and rounded[3] == pytest.approx(plain[3, 1])
    assert (rounded[1] - rounded[0]) / h == pytest.approx(plain[0, 0], rel=1e-3)
    assert (rounded[3] - rounded[2]) / h == pytest.approx(plain[3, 0], rel=1e-3)
    inside = np.linspace(a, b, 101)
    curve = continuous.rounded(study.design, inside, np.zeros(inside.size, bool), columns)[0]
    assert a < inside[curve.argmin()] < b and curve.min() < min(rounded[0], rounded[3])


def test_the_beam_is_rigid_inside_its_columns() -> None:
    """Between a column's face and its axis the beam does not bend: no curvature there, so the
    deflection runs straight from the axis to the face."""
    result = _run(continuous.EXAMPLE, design=False)
    diagrams = result["diagrams"]
    x, v = np.array(diagrams["x"]), np.array(diagrams["v_min"])
    inside = (x >= 5.0 - 1e-9) & (x <= 5.25 + 1e-9)
    slope = np.polyfit(x[inside], v[inside], 1)
    assert np.allclose(np.polyval(slope, x[inside]), v[inside], atol=1e-6)


def test_the_faces_are_the_columns() -> None:
    """The faces sit half of each column width from its axis, and so does the clear span."""
    result = _run(continuous.EXAMPLE, design=False)
    assert result["elements"][0]["faces"] == pytest.approx([0.15, 5.0 - 0.25])
    assert result["elements"][1]["faces"] == pytest.approx([5.25, 9.0 - 0.2])
    assert [row["clear"] for row in result["deflections"]] == pytest.approx([4.6, 3.55, 2.65])


@pytest.mark.parametrize(
    "changes",
    [{"columns": _columns([30, 30])}, {"columns": _columns([30] * 4, below=0.4)}, {"base": "hinged"}, {"above": "top"}],
)
def test_what_columns_refuse(changes: dict[str, Any]) -> None:
    """As many columns as supports, each taller than the beam is deep, a base and a column above
    the page knows."""
    result = _run(continuous.EXAMPLE | changes, design=False)
    assert not result["ok"] and result["field"] == "columns"


def test_the_steel_along_each_span() -> None:
    """At every station the steel the moment asks for stays under what was placed, and the
    middle zone stirrups are never closer than at the ends."""
    result = _run(continuous.EXAMPLE)
    for element, design in zip(result["elements"], result["designs"], strict=True):
        profile = design["profile"]
        assert len(profile["x"]) == continuous.PROFILE_STATIONS
        assert max(profile["As_bot_req"]) <= profile["As_bot"] + 1e-9
        assert max(profile["As_top_req"]) <= profile["As_top"] + 1e-9
        assert max(profile["Av_req"]) <= profile["Av"] + 1e-9
        zones = design["zones"]
        assert len(zones) == 3 and zones[0]["x0"] == pytest.approx(element["faces"][0])
        assert zones[1]["s"] >= zones[0]["s"]
    first = result["designs"][0]["profile"]
    assert first["As_bot_req"][len(first["x"]) // 2] > 0 and first["As_top_req"][-1] > 0


# ------------------------------------------------------------------- shift and bars as placed


def _heavy() -> dict[str, Any]:
    """The example four times as loaded: the top over B needs more than its base."""
    data = copy.deepcopy(continuous.EXAMPLE)
    for row in data["loads"]:
        row["value"] *= 4
    return data


def test_the_shifted_envelope_carries_the_peak_over_each_column() -> None:
    """Shifted by d, the envelope is at least as large everywhere, and over each column the
    hogging peak, rounded inside it, runs d to each side of where it is; both spans design that
    support for that peak, as its negative steel runs continuous over the column."""
    result = _run(continuous.EXAMPLE)
    d = result["diagrams"]
    for i in range(len(d["x"])):
        assert d["M_shift_max"][i] >= d["M_max"][i] - 1e-6 and d["M_shift_min"][i] <= d["M_min"][i] + 1e-6
    left, right = result["elements"][0]["faces"][1], result["elements"][1]["faces"][0]
    depth = result["elements"][0]["d"]
    peak, at = min((m, x) for x, m in zip(d["x"], d["M_min"], strict=True) if left <= x <= right)
    near = [i for i, x in enumerate(d["x"]) if abs(x - at) <= depth - 1e-3]
    assert all(d["M_shift_min"][i] == pytest.approx(peak, abs=1e-3) for i in near)
    first, second = result["designs"][0]["demands"], result["designs"][1]["demands"]
    assert first["M-r"]["M"] == second["M-l"]["M"] == pytest.approx(peak, abs=0.05)  # designed for the peak


def test_the_bars_as_placed_cover_what_the_shifted_moment_asks() -> None:
    """A base on each face along the whole span, two bars at least on top; additions where the
    shifted moment needs more, run on l_bII,min = f_yk/14·Ø past it; what is placed never falls
    under what is required."""
    result = _run(_heavy())
    for element, design in zip(result["elements"], result["designs"], strict=True):
        bars, profile = design["bars"], design["profile"]
        assert bars["top_base"]["bars"].startswith("2")
        for face in ("bot", "top"):
            placed, need = profile[f"As_{face}_placed"], profile[f"As_{face}_req"]
            assert all(have >= want - 1e-6 for have, want in zip(placed, need, strict=True)), (face, placed, need)
            for add in bars[f"{face}_add"]:
                assert element["faces"][0] - 1e-9 <= add["x0"] < add["x1"] <= element["faces"][1] + 1e-9
    over_b = [add for add in result["designs"][0]["bars"]["top_add"] if add["at"] == "right"]
    assert over_b and over_b[0]["x1"] == pytest.approx(result["elements"][0]["faces"][1])


def test_the_anchorage_drawn_is_fyk_over_14_bars() -> None:
    """EHE-08 §69.5.1.2: l_bII ≥ f_yk/14·Ø, 500/14·16 mm = 0.571 m for a bar of 16 under B500."""
    assert continuous.anchorage(continuous.EXAMPLE, 16) == pytest.approx(500 / 14 * 0.016)


def test_the_own_weight_is_given_to_the_page() -> None:
    result = _run(continuous.EXAMPLE, design=False)
    assert [element["self_weight"] for element in result["elements"]] == pytest.approx(
        [0.3 * 0.5 * 24.516625] * 3, rel=1e-3
    )
