"""The shear wall glue, exercised without a browser."""

import base64
import io
import json

import docx
import pytest

import wall

CODES = ["ACI 318-19", "CIRSOC 201-25", "EN 1992-2004"]


def solve(**changes):
    return json.loads(wall.run(json.dumps({**wall.EXAMPLE, **changes})))


@pytest.mark.parametrize("code", CODES)
def test_design_passes_for_every_code_that_checks_walls(code):
    result = solve(code=code)
    assert result["ok"], result
    assert result["rebar"]["horizontal"].startswith("Ø")
    assert result["ledger"][0]["dcr"] <= 1


def test_en_writes_its_own_symbols_and_checks_what_the_user_typed():
    result = solve(
        code="EN 1992-2004",
        fy=500,
        mode="check",
        rebar={"horizontal": {"d": 10, "s": 15}, "vertical": {"d": 10, "s": 20}},
    )
    assert result["ok"], result
    assert result["ledger"][0]["symbol"] == "VRd"
    assert result["tables"]["shear"]["columns"][-1] == "DCR" and "Ash" in result["tables"]["shear"]["columns"]


def test_the_worked_example_is_used_between_07_and_09():
    governing = solve()["ledger"][0]["dcr"]
    assert 0.5 <= governing <= 0.9, governing


def test_ledger_is_one_row_of_in_plane_shear():
    ledger = solve()["ledger"]
    assert [row["key"] for row in ledger] == ["shear_in_plane"]
    row = ledger[0]
    assert row["symbol"] in ("ØVn", "VRd") and row["unit"] == "kN"
    assert row["capacity"] > row["demand"] == 700.0
    assert row["combo"] == "1.2D+1.0E"


def test_both_meshes_are_proposed_and_only_the_horizontal_one_carries_a_dcr():
    options = solve()["options"]
    assert set(options) == {"horizontal", "vertical"}
    assert all(option["dcr"] is not None for option in options["horizontal"])
    # the vertical mesh is minimum steel: it has no DCR of its own (6)
    assert all(option["dcr"] is None for option in options["vertical"])


def test_alternatives_are_other_diameters_of_the_same_steel():
    options = solve()["options"]["horizontal"]
    assert len(options) > 1
    assert len({option["layout"]["d"] for option in options}) == len(options)
    areas = [float(option["area"].split()[0]) for option in options]
    assert max(areas) - min(areas) < max(areas) * 0.2
    for option in options:
        assert option["layout"]["s"] <= 45  # the code's limit for a wall mesh


def test_a_chosen_option_survives_a_recalculation():
    first = solve()
    wanted = first["options"]["horizontal"][1]
    again = solve(choice={"horizontal": wanted["signature"]})
    assert again["selected"]["horizontal"] == 1
    assert again["rebar"]["horizontal"] == wanted["bars"] and again["changed"] == []


def test_check_mode_takes_the_mesh_the_user_typed():
    result = solve(mode="check", rebar={"horizontal": {"d": 10, "s": 15}, "vertical": {"d": 8, "s": 20}})
    assert result["ok"], result
    assert result["rebar"] == {"horizontal": "Ø10 c/15 cm", "vertical": "Ø8 c/20 cm"}
    assert result["options"] == {}


def test_a_mesh_below_the_minimum_ratio_fails_as_mento_marks_it():
    result = solve(mode="check", rebar={"horizontal": {"d": 6, "s": 45}, "vertical": {"d": 6, "s": 45}})
    assert result["ok"], result
    ratios = [notice for notice in result["notices"] if notice["code"] == "mesh_ratio_below_min"]
    assert [notice["values"]["direction"] for notice in ratios] == ["h", "v"]
    assert all(notice["severity"] == "bad" for notice in ratios)


def test_the_worked_example_has_no_errors():
    assert not [notice for notice in solve()["notices"] if notice["severity"] == "bad"]


def test_the_drawing_is_a_horizontal_cut_with_two_curtains():
    section = solve()["section"]
    assert section["length"] == 300 and section["thickness"] == 20
    depths = {bar["y"] for bar in section["bars"]}
    assert len(depths) == 2  # one curtain against each face
    assert all(0 <= bar["x"] <= section["length"] for bar in section["bars"])


def test_report_is_the_shear_check_in_word():
    (file,) = json.loads(wall.report(json.dumps({**wall.EXAMPLE, "lang": "en", "label": "W1/2"})))
    assert file["name"] == "W1_2 - CIRSOC 201-25.docx"
    document = docx.Document(io.BytesIO(base64.b64decode(file["base64"])))
    assert "shear" in " ".join(paragraph.text.lower() for paragraph in document.paragraphs)
