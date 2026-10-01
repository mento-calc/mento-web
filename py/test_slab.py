"""The one-way slab glue, exercised without a browser."""

import base64
import io
import itertools
import json

import common
import docx
import pytest

import slab

CODES = ["ACI 318-19", "CIRSOC 201-25", "EN 1992-2004"]


def solve(**changes):
    return json.loads(slab.run(json.dumps({**slab.EXAMPLE, **changes})))


@pytest.mark.parametrize("code", CODES)
def test_design_passes_for_every_code(code):
    result = solve(code=code)
    assert result["ok"], result
    assert result["rebar"]["bot"].startswith("Ø")
    assert max(row["dcr"] or 0 for row in result["ledger"]) <= 1


def test_the_worked_example_is_used_between_07_and_09():
    """The first impression: a slab that passes, with room to spare but not too much (6)."""
    governing = max(row["dcr"] or 0 for row in solve()["ledger"])
    assert 0.7 <= governing <= 0.9, governing


def test_a_face_is_one_diameter_at_one_spacing():
    result = solve()
    assert result["layouts"]["bot"].keys() == {"d", "s"}
    assert result["rebar"]["bot"] == f"Ø{result['layouts']['bot']['d']:g}c/{result['layouts']['bot']['s']:g}cm"


def test_alternatives_are_the_meshes_mento_offers():
    result = solve()
    options = result["options"]["bot"]
    assert len(options) > 1
    assert options[0]["bars"] == result["rebar"]["bot"]  # mento's own pick leads
    assert len({option["signature"] for option in options}) == len(options)
    for option in options:
        assert 10 <= option["layout"]["s"] <= 40  # never tighter than 10 cm, never wider than 3h
        assert option["dcr"] <= 1 and option["complies"]  # mento offers only meshes that pass
    areas = [float(option["area"].split()[0]) for option in options]
    assert max(areas) - min(areas) < max(areas) * 0.25  # the same steel, laid out differently


def test_a_chosen_option_survives_a_recalculation():
    first = solve()
    wanted = first["options"]["bot"][1]
    again = solve(choice={"bot": wanted["signature"]})
    assert again["selected"]["bot"] == 1
    assert again["rebar"]["bot"] == wanted["bars"] and again["changed"] == []


def test_ledger_is_bottom_flexure_top_flexure_and_shear():
    ledger = solve()["ledger"]
    assert [row["key"] for row in ledger] == ["flexure_bottom", "flexure_top", "shear"]
    assert ledger[1]["combo"] == "1.2D+1.6L (apoyo)"  # the only combination with a negative moment
    assert ledger[2]["unit"] == "kN" and ledger[0]["unit"] == "kNm"


def test_check_mode_takes_the_mesh_the_user_typed():
    result = solve(mode="check", rebar={"bot": {"d": 12, "s": 15}, "top": {"d": 10, "s": 20}})
    assert result["ok"], result
    assert result["rebar"] == {"bot": "Ø12c/15cm", "top": "Ø10c/20cm"}
    assert result["options"] == {}


def test_a_strip_without_bottom_reinforcement_is_an_input_error():
    result = solve(mode="check", rebar={"bot": {}, "top": {"d": 10, "s": 20}})
    assert result == {"ok": False, "kind": "input", "field": "rebar", "message": "bottom"}


def test_the_drawing_places_a_bar_every_spacing():
    section = solve()["section"]
    bottom = sorted(bar["x"] for bar in section["bars"] if bar["y"] < section["height"] / 2)
    spacing = solve()["layouts"]["bot"]["s"]
    assert len(bottom) >= 2
    assert all(abs(second - first - spacing) < 1e-6 for first, second in itertools.pairwise(bottom))
    assert 0 <= bottom[0] and bottom[-1] <= section["width"]


def test_tables_are_mentos_own():
    tables = solve()["tables"]
    assert tables["flexure"]["columns"][:3] == ["Label", "Comb.", "Position"]
    assert tables["shear"]["columns"][-1] == "DCR"


def test_report_is_one_word_file_with_flexure_and_shear():
    (file,) = json.loads(slab.report(json.dumps({**slab.EXAMPLE, "lang": "en", "label": "L1/2"})))
    assert file["name"] == "L1_2 - CIRSOC 201-25.docx"
    document = docx.Document(io.BytesIO(base64.b64decode(file["base64"])))
    text = " ".join(paragraph.text.lower() for paragraph in document.paragraphs)
    assert "flexur" in text and "shear" in text


def test_the_worked_example_has_no_errors():
    result = solve()
    assert result["notices"] == [] and result["complies"]


def test_a_metre_of_10_at_15_is_seven_bars_and_524():
    """mento counts width / s bars for the area (6.67, 5.24 cm²) and places the whole ones (7)."""
    result = solve(mode="check", rebar={"bot": {"d": 10, "s": 15}, "top": {}})
    assert result["placed"]["bot"] == 7 and result["area"]["bot"] == "5.24 cm²/m"
    assert result["rebar"]["bot"] == "Ø10c/15cm"
    assert len([bar for bar in result["section"]["bars"] if bar["y"] < result["section"]["height"] / 2]) == 7


def test_with_no_moment_only_the_bottom_needs_its_minimum():
    rebar = {"bot": {"d": 6, "s": 30}, "top": {"d": 6, "s": 30}}
    result = solve(mode="check", code="ACI 318-19", rebar=rebar, forces=[{"label": "U", "M_y": 0, "V_z": 30}])
    minimum = [notice for notice in result["notices"] if notice["code"] == "As_below_min"]
    assert [notice["values"]["face"] for notice in minimum] == ["bottom"]


def test_bars_closer_than_the_minimum_clear_spacing_fail():
    result = solve(mode="check", rebar={"bot": {"d": 16, "s": 3}, "top": {"d": 12, "s": 25}})
    spacing = [notice for notice in result["notices"] if notice["code"] == "bar_spacing_below_min"]
    assert spacing and spacing[0]["severity"] == "bad" and spacing[0]["values"]["face"] == "bottom"


# ------------------------------------------------------------------ the slab list (Excel)


def saved(**changes):
    """A slab as the page saves it in its list: the payload it sent, with the layouts it got back."""
    data = {**slab.EXAMPLE, **changes}
    result = json.loads(slab.run(json.dumps(data)))
    assert result["ok"], result
    return {**data, "layouts": result["layouts"]}


def sheet(slabs):
    import pandas as pd

    (file,) = json.loads(slab.summary(json.dumps({"lang": "es", "slabs": slabs})))
    return pd.read_excel(io.BytesIO(base64.b64decode(file["base64"])), sheet_name="Slabs")


def test_the_excel_has_the_summary_columns_and_a_row_for_every_combination():
    frame = sheet([saved(), saved(label="L-102", height=25)])
    assert list(frame.columns) == slab.SUMMARY_COLUMNS
    assert frame.iloc[0].tolist()[2:] == ["cm", "cm", "mm", "kN", "kN", "kNm", "mm", "cm", "mm", "cm"]
    assert frame["Label"].tolist()[1:] == ["L-101"] * 2 + ["L-102"] * 2
    assert frame["h"].tolist()[1:] == [20, 20, 25, 25]


def test_each_row_carries_the_face_its_moment_pulls():
    data = saved()
    layouts = data["layouts"]
    frame = sheet([data]).iloc[1:].reset_index(drop=True)
    assert frame["My"].tolist() == [32, -22]
    for _, row in frame.iterrows():
        face = layouts["bot" if row["My"] >= 0 else "top"]
        assert [row["db1"], row["s1"], row["db3"], row["s3"]] == [face["d"], face["s"], 0, 0]


def test_slabs_of_other_materials_do_not_share_an_excel():
    with pytest.raises(common.InputError) as error:
        sheet([saved(), saved(label="L-102", fy=500)])
    assert error.value.field == "list"
    with pytest.raises(common.InputError):
        sheet([])


def test_a_us_list_writes_inches_and_the_bar_diameters():
    data = saved(**{k: v for k, v in slab.EXAMPLE_US.items() if k != "lang"})
    frame = sheet([data])
    assert frame.iloc[0].tolist()[2:] == ["in", "in", "in", "kip", "kip", "kip·ft", "in", "in", "in", "in"]
    assert frame.iloc[1]["db1"] == data["layouts"]["bot"]["d"] * 0.125  # #3 is 0.375 in


def test_mento_reads_the_excel():
    """From mento 1.5.0 OneWaySlabSummary reads it and checks every row; before it, there is nothing to read it."""
    import mento

    summary = getattr(mento, "OneWaySlabSummary", None)
    if summary is None:
        pytest.skip(f"mento {mento.__version__} has no OneWaySlabSummary")
    data = saved()
    concrete, steel = common.materials(data)
    checked = summary(concrete, steel, sheet([data])).check()
    assert len(checked) >= 1
