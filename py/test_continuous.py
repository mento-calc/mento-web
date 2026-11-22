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
import continuous
import numpy as np
import pytest
from mento.design_results import FlexureFaceDesign

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
    """Every example the site opens passes, its governing DCR between 0.7 and 0.9, and the
    redistribution it asks for is within what the code allows over each interior support."""
    result = _run(getattr(continuous, example))
    assert result["ok"] and result["complies"]
    assert 0.7 <= result["dcr"] <= 0.9, result["dcr"]
    checks = [row["redistribution"] for row in result["supports"] if row["redistribution"]]
    assert [row["name"] for row in result["supports"] if row["redistribution"]] == ["B", "C"]
    assert all(check["ok"] and check["requested"] <= check["max"] for check in checks)


def test_the_analysis_alone_answers_without_designing() -> None:
    result = _run(continuous.EXAMPLE, design=False)
    assert result["ok"] and "designs" not in result
    assert result["arrangements"] == 2**3 + 1  # 1.4D, and 1.2D + 1.6L on every subset of three spans
    assert len(result["diagrams"]["x"]) == len(result["diagrams"]["M_max"]) == len(result["diagrams"]["v_min"])


# ---------------------------------------------------------------------- what reaches mento


def test_two_spans_reach_mento_with_their_closed_forms() -> None:
    """Dead load alone, so 1.4D governs: −1.4·qL²/8 over the middle support and 1.4·9qL²/128 in
    each span; the shear there 1.4·5qL/8."""
    result = _run(_two_spans())
    first = result["designs"][0]["demands"]
    assert first["M+"]["M"] == pytest.approx(1.4 * 9 * q * L**2 / 128, abs=0.01)
    assert first["M-r"]["M"] == pytest.approx(-1.4 * q * L**2 / 8, abs=0.01)
    assert first["M-r"]["V"] == pytest.approx(1.4 * 5 * q * L / 8, abs=0.01)
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
    data = _two_spans(spans=[{"L": L, "width": 20, "height": 50}] * 3)
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
    assert row["deflection"] == pytest.approx(5 * 15.0 * 6.0**4 / (384 * EI) * 1e3, abs=0.01)
    assert row["allowed"] == pytest.approx(6000 / 250) and row["ok"]


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
    result = _run(continuous.EXAMPLE | {"redistribution": 25})
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
    checks = [row["redistribution"] for row in _run(data)["supports"] if row["redistribution"]]
    assert all(not check["ok"] and check["reason"] == "spans" and check["max"] == 0 for check in checks)


def test_an_overhang_is_designed_for_its_support_and_never_redistributed() -> None:
    data = copy.deepcopy(continuous.EXAMPLE) | {"redistribution": 0}
    data["ends"]["right"] = {"type": "overhang", "L": 1.5, "width": 20, "height": 55}
    data["loads"].append({"case": "D", "type": "point", "element": 3, "value": 10.0, "a": 1.5})
    result = _run(data)
    overhang = result["designs"][-1]["demands"]
    w = 1.4 * 0.2 * 0.55 * 2500 * 9.80665 / 1000  # its own weight: the example loads only the spans
    support = 1.5 - 0.1  # its face, half of the 20 cm support from the axis
    assert set(overhang) == {"M+", "M-o"}
    assert overhang["M-o"]["M"] == pytest.approx(-(w * support**2 / 2 + 1.4 * 10.0 * support), abs=0.01)
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
