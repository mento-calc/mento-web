"""The glue is plain Python, so it is tested without a browser."""

import base64
import io
import json

import common
import docx
import pytest

import beam

CODES = ["ACI 318-19", "CIRSOC 201-25", "EN 1992-2004"]
FIT = ("clear_spacing_below_min", "clear_spacing_below_vibrator", "bars_do_not_fit")


def solve(**changes):
    return json.loads(beam.run(json.dumps({**beam.EXAMPLE, **changes})))


@pytest.mark.parametrize("code", CODES)
def test_design_passes_for_every_code(code):
    result = solve(code=code)
    assert result["ok"], result
    assert result["flexure"]["bottom"]["bars"]
    assert result["flexure"]["bottom"]["DCR"] <= 1
    assert result["shear"]["DCR"] <= 1
    assert "svg" not in result  # the page draws the section itself; matplotlib is never loaded there


def test_section_places_every_bar_inside_the_stirrup():
    rebar = {
        "bot": {"n1": 2, "d1": 16, "n2": 1, "d2": 12},
        "top": {"n1": 2, "d1": 10},
        "stirrups": {"n": 2, "d": 8, "s": 15},
    }
    section = solve(mode="check", rebar=rebar)["section"]
    assert section["stirrup"] == {"d": 0.8, "bend": 3.2}  # a Ø8 bent on four diameters (Table 25.3.2)
    main = [bar for bar in section["bars"] if bar["kind"] == "main"]
    assert sorted(bar["d"] for bar in main) == [1.0, 1.0, 1.2, 1.6, 1.6]
    edge = section["cover"] + section["stirrup"]["d"]
    for bar in section["bars"]:
        assert edge <= bar["x"] - bar["d"] / 2 + 1e-9 and bar["x"] + bar["d"] / 2 <= section["width"] - edge + 1e-9
        assert edge <= bar["y"] - bar["d"] / 2 + 1e-9 and bar["y"] + bar["d"] / 2 <= section["height"] - edge + 1e-9
    bottom = [bar for bar in main if bar["y"] < section["height"] / 2]
    assert [bar["x"] for bar in sorted(bottom, key=lambda bar: bar["x"])][1] == section["width"] / 2


NARROW = {"width": 12, "height": 30, "forces": [{"label": "U", "M_y": 30, "V_z": 50}]}


def labels(section, kind):
    return [label["bars"] for label in section["labels"] if label["kind"] == kind]


def test_section_moves_bars_that_do_not_fit_to_a_second_row():
    result = solve(**NARROW)
    assert result["ok"], result
    bottom = [bar for bar in result["section"]["bars"] if bar["y"] < result["section"]["height"] / 2]
    assert len({bar["y"] for bar in bottom}) == 2, result["flexure"]["bottom"]["bars"]


def test_a_designed_second_row_stays_in_the_second_row():
    # mento puts 2Ø12 and 2Ø10 in two rows here (n_1 and n_3); its options list only the groups
    # that carry bars, and read as n_1 + n_2 they would be four bars in one row of a 12 cm web
    result = solve(**NARROW)
    assert result["layouts"]["bot"] == {"n1": 2, "d1": 12.0, "n3": 2, "d3": 10.0}
    assert result["rows"]["bot"] == ["2Ø12", "2Ø10"]
    assert labels(result["section"], "bot") == ["2Ø12", "2Ø10"]
    assert result["complies"] and not any(notice["code"] in FIT for notice in result["notices"])


def test_a_design_that_does_not_close_says_the_section_is_too_small():
    """No layout that fits a 12 cm web carries 40 kNm: mento keeps the closest and says what it carries."""
    result = solve(**{**NARROW, "forces": [{"label": "U", "M_y": 40, "V_z": 50}]})
    assert result["ok"] and not result["complies"]
    assert result["ledger"][0]["dcr"] > 1
    codes = {notice["code"]: notice for notice in result["notices"]}
    assert {"As_below_required", "section_too_small_for_moment"} <= set(codes)
    assert codes["section_too_small_for_moment"]["severity"] == "bad"
    assert "40 kN" in codes["section_too_small_for_moment"]["message"]


def test_groups_that_fit_one_row_stay_in_it():
    result = solve()
    assert result["layouts"]["bot"] == {"n1": 2, "d1": 16.0, "n2": 1, "d2": 12.0}
    for option in result["options"]["bot"]:
        assert len(option["rows"]) == 1, option  # a 20 cm web takes each of them in one row


def test_check_mode_takes_a_second_row_on_each_face():
    rebar = {
        "bot": {"n1": 3, "d1": 20, "n3": 2, "d3": 16},
        "top": {"n1": 2, "d1": 12, "n3": 2, "d3": 10},
        "stirrups": {"n": 2, "d": 8, "s": 15},
    }
    result = solve(mode="check", rebar=rebar, width=20, height=40)
    assert result["layouts"]["bot"] == {"n1": 3, "d1": 20.0, "n3": 2, "d3": 16.0}
    assert result["rows"] == {"bot": ["3Ø20", "2Ø16"], "top": ["2Ø12", "2Ø10"], "st": ["1eØ8/15cm"]}
    first, second = (label["y"] for label in result["section"]["labels"] if label["kind"] == "bot")
    assert second - first > 2  # the second row sits a bar and a row gap above the first


def test_bars_that_do_not_fit_one_row_fail_as_mento_says():
    rebar = {
        "bot": {"n1": 3, "d1": 20, "n2": 2, "d2": 16},
        "top": {"n1": 2, "d1": 12},
        "stirrups": {"n": 2, "d": 8, "s": 15},
    }
    result = solve(mode="check", rebar=rebar, width=20, height=40, forces=[{"label": "U", "M_y": 60, "V_z": 50}])
    spacing = [notice for notice in result["notices"] if notice["code"] in FIT]
    assert spacing and spacing[0]["severity"] == "bad" and spacing[0]["values"]["face"] == "bottom"
    assert spacing[0]["message"]  # mento's own sentence, in the page's language


DOUBLY = {
    "width": 20,
    "height": 40,
    "forces": [{"label": "U1", "M_y": 150, "V_z": 50}, {"label": "U2", "M_y": -20, "V_z": 40}],
}


def test_a_doubly_reinforced_design_complies_past_the_singly_reinforced_maximum():
    result = solve(**DOUBLY)
    bottom = result["flexure"]["bottom"]
    assert float(bottom["A_s"].split()[0]) > float(bottom["A_s_max"].split()[0])  # past the singly reinforced limit
    assert result["complies"] and bottom["complies"]  # its top steel keeps it tension-controlled
    assert result["notices"] == []


def test_top_steel_short_of_the_compression_a_doubly_reinforced_beam_needs_fails():
    rebar = {
        "bot": {"n1": 3, "d1": 20, "n3": 2, "d3": 16},
        "top": {"n1": 2, "d1": 10},
        "stirrups": {"n": 2, "d": 8, "s": 15},
    }
    result = solve(mode="check", rebar=rebar, **DOUBLY)
    (short,) = [notice for notice in result["notices"] if notice["code"] == "not_tension_controlled"]
    assert short["severity"] == "bad"
    assert short["values"] == {"face": "bottom", "combos": ["U1"]}
    assert not result["complies"] and not result["flexure"]["bottom"]["complies"]


ACI_OVER_MAX = {
    "code": "ACI 318-19",
    "mode": "check",
    "width": 25,
    "height": 40,
    "forces": [{"label": "U", "M_y": 100, "V_z": 50}],
    "rebar": {"bot": {"n1": 3, "d1": 25, "n3": 3, "d3": 25}, "top": {}, "stirrups": {"n": 2, "d": 8, "s": 15}},
}


def test_a_section_that_is_not_tension_controlled_fails_with_a_dcr_below_one():
    """ACI 318-19 §9.3.3.1: 6Ø25 in a 25x40 beam carries 100 kNm (DCR 0.67) but does not comply."""
    result = solve(**ACI_OVER_MAX)
    bottom = next(row for row in result["ledger"] if row["key"] == "flexure_bottom")
    assert bottom["dcr"] == 0.669 and bottom["complies"] is False
    assert result["flexure"]["bottom"]["DCR"] < 1 and not result["flexure"]["bottom"]["complies"]
    assert result["complies"] is False
    (notice,) = result["notices"]
    assert notice["code"] == "not_tension_controlled" and notice["severity"] == "bad"
    assert "9.3.3.1" in notice["message"]


def test_the_worked_example_has_no_errors():
    assert not [notice for notice in solve()["notices"] if notice["severity"] == "bad"]


def test_check_mode_reports_insufficient_rebar():
    rebar = {"bot": {"n1": 2, "d1": 10}, "top": {}, "stirrups": {"n": 2, "d": 6, "s": 20}}
    result = solve(mode="check", rebar=rebar)
    assert result["ok"], result
    assert result["flexure"]["bottom"]["bars"] == "2Ø10"
    assert result["flexure"]["bottom"]["DCR"] > 1


def test_same_diameter_bars_are_counted_together():
    rebar = {"bot": {"n1": 2, "d1": 16, "n2": 1, "d2": 16}, "top": {}, "stirrups": {}}
    assert solve(mode="check", rebar=rebar)["flexure"]["bottom"]["bars"] == "3Ø16"


@pytest.mark.parametrize("field", ["fc", "fy", "width", "height", "cover"])
def test_bad_number_names_the_field(field):
    result = solve(**{field: ""})
    assert result == {"ok": False, "kind": "input", "field": field, "message": "missing"}


def test_no_forces_is_an_input_error():
    assert solve(forces=[{"M_y": 0, "V_z": 0}])["field"] == "forces"


def test_report_is_one_word_file_with_flexure_and_shear():
    (file,) = json.loads(beam.report(json.dumps({**beam.EXAMPLE, "code": "ACI 318-19", "lang": "en", "label": "V1/2"})))
    assert file["name"] == "V1_2 - ACI 318-19.docx"
    document = docx.Document(io.BytesIO(base64.b64decode(file["base64"])))
    text = " ".join(paragraph.text.lower() for paragraph in document.paragraphs)
    assert "flexur" in text and "shear" in text
    assert len(document.tables) >= 8


def test_design_offers_alternatives_with_their_own_dcr():
    result = solve()
    bottom = result["options"]["bot"]
    assert len(bottom) > 1, bottom
    assert bottom[0]["bars"] == result["rebar"]["bot"]  # mento's own pick leads
    assert bottom[0]["dcr"] == result["flexure"]["bottom"]["DCR"]
    assert len({option["signature"] for option in bottom}) == len(bottom)
    for option in bottom:
        assert option["dcr"] > 0 and option["area"].endswith("cm²")
    # more steel in a face can only lower that face's DCR
    by_area = sorted(bottom, key=lambda option: float(option["area"].split()[0]))
    assert [option["dcr"] for option in by_area] == sorted((o["dcr"] for o in by_area), reverse=True)


def test_a_chosen_option_survives_a_recalculation():
    first = solve()
    wanted = first["options"]["bot"][1]
    again = solve(choice={"bot": wanted["signature"]})
    assert again["selected"]["bot"] == 1
    assert again["rebar"]["bot"] == wanted["bars"]
    assert again["ledger"][0]["dcr"] == wanted["dcr"]
    assert again["changed"] == []


def test_a_choice_that_no_longer_exists_falls_back_and_says_so():
    result = solve(choice={"bot": "9x32"})
    assert result["selected"]["bot"] == 0
    assert result["changed"] == ["bot"]


def test_ledger_is_three_rows_capacity_demand_and_governing_combination():
    ledger = solve()["ledger"]
    assert [row["key"] for row in ledger] == ["flexure_bottom", "flexure_top", "shear"]
    bottom, top, shear = ledger
    assert bottom["combo"] == "1.2D+1.6L" and bottom["symbol"] == "ØMn"
    assert bottom["capacity"] > bottom["demand"] == 92.0 and bottom["unit"] == "kNm"
    assert top["combo"] == "0.9D+1.0E"  # the only combination that puts the top in tension
    assert shear["symbol"] == "ØVn" and shear["demand"] == 70.0 and shear["unit"] == "kN"
    assert bottom["complies"] and top["complies"]
    assert max(row["dcr"] for row in ledger) == max(
        solve()["flexure"]["bottom"]["DCR"], solve()["shear"]["DCR"], solve()["flexure"]["top"]["DCR"]
    )


def test_a_face_without_demand_has_no_dcr_and_no_bars_but_the_mounting_ones():
    """A beam that only sags is designed with no top steel: mento holds its stirrups there with
    mounting bars, which the drawing shows and no resistance counts."""
    result = solve(forces=[{"label": "U", "M_y": 92, "V_z": 95}])
    top = next(row for row in result["ledger"] if row["key"] == "flexure_top")
    assert top["dcr"] is None and top["combo"] is None and not top["capacity"]
    assert result["rebar"]["top"] == "" and result["flexure"]["top"]["A_s"] == "0.00 cm²"
    assert result["mounting"] == [{"face": "top", "bars": "2Ø8"}]
    mounting = [bar for bar in result["section"]["bars"] if bar["kind"] == "mounting"]
    assert len(mounting) == 2 and all(bar["y"] > 50 and bar["d"] == 0.8 for bar in mounting)
    assert labels(result["section"], "mounting") == ["2Ø8"] and labels(result["section"], "top") == []
    assert result["complies"] and result["notices"] == []


def test_check_mode_with_no_top_bars_has_none():
    """What is typed is what is checked: an empty top face is not the 2Ø8 a new mento beam starts with."""
    rebar = {"bot": {"n1": 3, "d1": 16}, "top": {}, "stirrups": {"n": 2, "d": 6, "s": 13}}
    result = solve(mode="check", rebar=rebar)
    assert result["flexure"]["top"]["bars"] == "" and result["mounting"] == [{"face": "top", "bars": "2Ø8"}]
    # the example's third combination puts the top in tension, and nothing is there to take it
    assert not result["complies"] and not result["flexure"]["top"]["complies"]


def test_the_minimum_is_the_effective_one():
    """The example's top face, 2Ø12+1Ø10 under Mu = -45 kNm, is below A_s,min but above the
    A_s,min,eff the 4/3 of CIRSOC 201-25 §9.6.1.3 leaves: no warning, and it complies."""
    result = solve()
    top = result["flexure"]["top"]
    assert top["bars"] == "2Ø12+1Ø10"
    area = float(top["A_s"].split()[0])
    assert float(top["A_s_min_eff"].split()[0]) <= area < float(top["A_s_min"].split()[0])
    assert top["complies"] and result["notices"] == []


def test_steel_below_the_effective_minimum_fails():
    rebar = {"bot": {"n1": 2, "d1": 8}, "top": {}, "stirrups": {"n": 2, "d": 6, "s": 20}}
    # 2Ø8 carries 18 kNm, but is short of 4/3 of what that moment asks for and of the minimum
    result = solve(mode="check", rebar=rebar, forces=[{"label": "U", "M_y": 18, "V_z": 20}])
    assert result["flexure"]["bottom"]["DCR"] < 1
    (notice,) = [notice for notice in result["notices"] if notice["code"] == "As_below_min"]
    assert notice["severity"] == "bad" and notice["values"]["face"] == "bottom"


def test_tables_keep_mentos_columns_with_the_units_in_the_header():
    tables = solve()["tables"]
    flexure = tables["flexure"]
    assert flexure["columns"][:3] == ["Label", "Comb.", "Position"]
    assert flexure["columns"][flexure["dcr"]] == "DCR"
    assert flexure["units"][3] == "cm²"
    assert len(flexure["rows"]) == len(beam.EXAMPLE["forces"])
    assert tables["shear"]["columns"][-1] == "DCR"


def test_detailed_results_come_as_tables_too():
    result = solve(lang="en")
    flexure, shear = result["reports"]
    assert flexure["title"] == "BEAM FLEXURE DETAILED RESULTS"
    assert [table["title"] for table in flexure["tables"]][:3] == ["Materials", "Geometry", "Design forces"]
    materials = flexure["tables"][0]
    assert materials["columns"] == ["Variable", "Value", "Unit"]
    assert ["Concrete strength", "fc", "25.0", "MPa"] in materials["rows"]
    check = flexure["tables"][3]
    assert check["columns"] == ["Unit", "Value", "Min.", "Max.", "Ok?"]
    assert all(len(row) == 6 for row in check["rows"])
    assert shear["tables"][-2]["rows"][-1][1:3] == ["DCR", "0.65"]
    assert ["Number of legs", "nl", "2", ""] in shear["tables"][3]["rows"]
    # mento closes the shear report with the two states it keeps apart: resistance and detailing
    assert shear["tables"][-1] == {
        "title": "Verification",
        "columns": ["Status", "Detailing notes"],
        "rows": [
            ["Resistance", "Pass (modelled checks)", ""],
            ["Detailing (modelled checks)", "Pass (modelled checks)", ""],
        ],
    }
    assert "BEAM FLEXURE DETAILED RESULTS" in result["detailed"]  # the text stays, for Copiá


def test_a_printed_table_is_cut_where_each_column_starts():
    text = "===== T =====\nName      Variable    Value  Unit\n--------  ----------  -----  ----\nWidth         b          20  cm\nCheck                    ✅\n\n"
    (report,) = common.reports(text)
    assert report["tables"] == [
        {
            "title": "Name",
            "columns": ["Variable", "Value", "Unit"],
            "rows": [["Width", "b", "20", "cm"], ["Check", "", "✅", ""]],
        },
    ]


def test_check_mode_has_no_options():
    rebar = {"bot": {"n1": 3, "d1": 16}, "top": {"n1": 2, "d1": 12}, "stirrups": {"n": 2, "d": 6, "s": 13}}
    result = solve(mode="check", rebar=rebar)
    assert result["options"] == {} and result["selected"] == {}
    assert result["rebar"] == {"bot": "3Ø16", "top": "2Ø12", "st": "1eØ6/13cm"}


# ---------------------------------------------------------------- legs, skin and mounting bars


def test_stirrups_are_counted_by_their_legs():
    """As mento counts them, two per closed stirrup, and written as mento writes them: the stirrup
    and the legs beyond it, in the page's language (``1e`` is one estribo, ``1s`` one stirrup)."""
    result = solve()
    assert result["layouts"]["st"] == {"n": 2, "d": 6.0, "s": 28.0}
    assert result["rebar"]["st"] == result["shear"]["stirrups"] == "1eØ6/28cm"
    assert [option["signature"] for option in result["options"]["st"]] == ["2x6@28", "2x8@27", "2x10@27"]
    assert result["options"]["st"][0]["area"] == "2.02 cm²/m"  # two legs of Ø6 every 28 cm
    assert solve(lang="en")["rebar"]["st"] == "1sØ6/28cm"


def test_one_leg_is_an_input_error():
    rebar = {"bot": {"n1": 3, "d1": 16}, "top": {}, "stirrups": {"n": 1, "d": 6, "s": 13}}
    assert solve(mode="check", rebar=rebar) == {"ok": False, "kind": "input", "field": "rebar", "message": "legs"}


WIDE = {"code": "ACI 318-19", "width": 60, "height": 40, "forces": [{"label": "U", "M_y": 250, "V_z": 300, "N_x": 0}]}


def test_a_wide_beam_is_designed_with_four_legs_and_a_bar_at_each():
    """One perimeter stirrup and two legs between its own, each holding a bar on both faces: seven
    bottom bars for four legs, and mounting bars on the face no combination puts in tension."""
    result = solve(**WIDE)
    section = result["section"]
    assert result["rebar"] == {"bot": "7Ø20", "top": "", "st": "1e+2rØ10/17cm"}  # a stirrup and two legs
    assert len(section["closed"]) == 1 and len(section["ties"]) == 2
    (perimeter,) = section["closed"]
    centre = section["cover"] + section["stirrup"]["d"] / 2  # the centreline of the perimeter stirrup
    assert (perimeter["x0"], perimeter["y0"]) == (centre, centre)
    assert (perimeter["x1"], perimeter["y1"]) == (section["width"] - centre, section["height"] - centre)
    assert [tie["x"] for tie in section["ties"]] == [21.0, 39.0]  # evenly between the outer legs
    assert [tie["side"] for tie in section["ties"]] == [1, -1]  # their hooks open towards the middle
    assert result["mounting"] == [{"face": "top", "bars": "4Ø8"}]
    assert result["complies"] and result["notices"] == []


def test_check_mode_takes_an_odd_number_of_legs():
    rebar = {"bot": {"n1": 3, "d1": 16}, "top": {"n1": 2, "d1": 12}, "stirrups": {"n": 3, "d": 8, "s": 15}}
    result = solve(mode="check", rebar=rebar, width=30, forces=[{"label": "U", "M_y": 120, "V_z": 150}])
    assert result["rebar"]["st"] == "1e+1rØ8/15cm" and result["shear"]["A_v"] == "10.05 cm²/m"
    assert [tie["x"] for tie in result["section"]["ties"]] == [15.0]
    # the leg in the middle finds a bar at the bottom; on top, between the two corner bars, a mounting one
    assert result["mounting"] == [{"face": "top", "bars": "1Ø8"}]


def test_a_beam_60_cm_deep_gets_the_skin_mento_proposes():
    result = solve()
    assert result["skin"] == {"bars": "1Ø8", "status": "proposed"}
    skin = [bar for bar in result["section"]["bars"] if bar["kind"] == "skin"]
    assert len(skin) == 2 and {bar["x"] for bar in skin} == {3.5, 16.5}  # one against each side face
    assert labels(result["section"], "skin") == ["1Ø8"]
    assert result["notices"] == []  # proposed, not required: nothing to say
    assert solve(height=50)["skin"] is None


def test_skin_the_code_requires_is_a_note_not_a_failure():
    """CIRSOC 201-25 §9.7.2.3 asks for skin above 90 cm; mento lays it out and says so."""
    forces = [{"label": "U1", "M_y": 700, "V_z": 300}, {"label": "U2", "M_y": -500, "V_z": 300}]
    result = solve(width=30, height=120, forces=forces)
    assert result["skin"] == {"bars": "3Ø10", "status": "required"}
    assert len([bar for bar in result["section"]["bars"] if bar["kind"] == "skin"]) == 6
    (notice,) = result["notices"]
    assert (notice["code"], notice["severity"]) == ("skin_reinforcement_required", "info")
    assert result["complies"]


def test_the_drawing_is_the_cage_mento_details():
    """Corner bars seated in the bend of the stirrup, as ``beam.detailing_geometry`` places them."""
    section = solve()["section"]
    bottom = [bar for bar in section["bars"] if bar["kind"] == "main" and bar["y"] < section["height"] / 2]
    corner = min(bottom, key=lambda bar: bar["x"])
    # a Ø16 in the 24 mm bend of a Ø6: 12 - (12 - 8) / √2 = 9.17 mm from the inner faces of the stirrup
    inner = section["cover"] + section["stirrup"]["d"]
    assert corner == {"x": round(inner + 0.917, 3), "y": round(inner + 0.917, 3), "d": 1.6, "kind": "main"}
    assert section["steel"] == "70 kg/m³"


def test_a_cage_that_cannot_be_detailed_fails_and_is_drawn_as_calculated():
    """Two bars for four legs, 31 cm apart: mento cannot build that cage, and says so."""
    rebar = {"bot": {"n1": 2, "d1": 20}, "top": {}, "stirrups": {"n": 4, "d": 8, "s": 15}}
    result = solve(mode="check", rebar=rebar, width=40, forces=[{"label": "U", "M_y": 120, "V_z": 150}])
    assert result["ok"], result
    codes = {notice["code"]: notice["severity"] for notice in result["notices"]}
    assert codes["cage_detailing_infeasible"] == codes["bar_spacing_exceeds_max"] == "bad"
    section = result["section"]
    assert len(section["ties"]) == 2 and not [bar for bar in section["bars"] if bar["kind"] != "main"]


def test_report_joins_flexure_and_shear_without_a_blank_page():
    """The shear part starts on its own page, and nothing empty is left before it to spill over."""
    from docx.oxml.ns import qn

    (file,) = json.loads(beam.report(json.dumps({**beam.EXAMPLE, "lang": "en"})))
    body = docx.Document(io.BytesIO(base64.b64decode(file["base64"]))).element.body
    elements = list(body)
    breaks = [index for index, element in enumerate(elements) if list(element.iter(qn("w:pageBreakBefore")))]
    assert len(breaks) == 1, "one join, one page break"
    assert not [br for br in body.iter(qn("w:br")) if br.get(qn("w:type")) == "page"], "no break paragraphs"
    before = elements[breaks[0] - 1]
    assert before.tag == qn("w:tbl"), "the flexure part ends on its last table, not on an empty paragraph"
    title = "".join(node.text or "" for node in elements[breaks[0]].iter(qn("w:t")))
    assert "shear" in title.lower()
