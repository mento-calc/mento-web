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


US_SLAB = {key: value for key, value in slab.EXAMPLE_US.items() if key != "lang"}


def excel(slabs):
    file = json.loads(slab.summary(json.dumps({"lang": "es", "slabs": slabs})))
    assert file["ok"], file
    assert file["name"] == "slabs.xlsx"
    return io.BytesIO(base64.b64decode(file["base64"]))


def sheets(slabs):
    import pandas as pd

    return pd.read_excel(excel(slabs), sheet_name=None)


def test_the_excel_is_the_two_sheets_the_slab_summary_reads():
    book = sheets([saved(), saved(label="L-102", height=25)])
    assert list(book) == ["Sections", "Forces"]
    sections, forces = book["Sections"], book["Forces"]
    assert sections["Label"].tolist()[1:] == ["L-101", "L-102"]
    assert sections["h"].tolist()[1:] == [20, 25]
    assert sections[["b", "h", "cc", "db1_bot", "s1_bot"]].iloc[0].tolist() == ["cm", "cm", "mm", "mm", "cm"]
    assert forces["Label"].tolist()[1:] == ["L-101"] * 2 + ["L-102"] * 2
    assert forces["My"].tolist()[1:3] == [32, -22]


def test_a_section_carries_the_mesh_of_both_faces():
    data = saved()
    layouts = data["layouts"]
    row = sheets([data])["Sections"].iloc[1]
    for face in ("bot", "top"):
        assert [row[f"db1_{face}"], row[f"s1_{face}"]] == [layouts[face]["d"], layouts[face]["s"]]
        assert [row[f"db3_{face}"], row[f"s3_{face}"]] == [0, 0]  # the page gives one layer per face


def test_mento_reads_the_excel_and_checks_what_the_page_checked():
    from mento import OneWaySlabSummary

    for data in (saved(), saved(**US_SLAB)):
        page = {row["key"]: row["dcr"] for row in json.loads(slab.run(json.dumps(data)))["ledger"]}
        concrete, steel = common.materials(data)
        summary = OneWaySlabSummary.from_excel(concrete, steel, excel([data]))
        summary.check()
        (verdict,) = summary.results
        assert verdict.passes
        got = {"flexure_bottom": verdict.bottom.DCR, "flexure_top": verdict.top.DCR, "shear": verdict.shear.DCR}
        assert {key: round(value, 3) for key, value in got.items()} == page


def test_a_us_list_is_written_in_inches_and_kip():
    book = sheets([saved(**US_SLAB)])
    assert book["Sections"][["b", "h", "cc", "db1_bot", "s1_bot"]].iloc[0].tolist() == ["in"] * 5
    assert book["Forces"][["Nx", "Vz", "My"]].iloc[0].tolist() == ["kip", "kip", "kip·ft"]
    assert book["Sections"]["db1_bot"].iloc[1] == 0.375  # a #3


def test_slabs_of_other_materials_do_not_share_an_excel():
    def failed(slabs):
        return json.loads(slab.summary(json.dumps({"lang": "es", "slabs": slabs})))

    assert failed([saved(), saved(label="L-102", fy=500)]) == {
        "ok": False,
        "kind": "input",
        "field": "list",
        "message": "materials",
    }
    assert failed([])["message"] == "empty"
