"""The static pages against the dictionaries, the pinned mento and the worked example."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import mento
import pandas as pd
import pytest
from common import signature
from mento import BeamSummary, Concrete_ACI_318_19, MPa, SteelBar

import beam

ROOT = Path(__file__).resolve().parent.parent
PAGES = {
    "home": ROOT / "index.html",
    "beam": ROOT / "beam" / "index.html",
    "slab": ROOT / "slab" / "index.html",
    "wall": ROOT / "wall" / "index.html",
}
LANGS = ("es", "en")
# Engineering notation, the same in every language: numbers and areas, bar layouts such as
# "2Ø16+1Ø12" or "1eØ6/28cm", the symbols the design system uses as field labels (M, V, c/, eØ) and units.
NOTATION = re.compile(r"[\d.]+( cm²)?|\d+Ø\d+(\+\d+Ø\d+)*|[A-Za-zØ+×·/−]{1,3}|cm|mm|MPa|kN|kNm|cm²|· mento")


class _Strings(HTMLParser):
    """Collects every i18n key of a page, and the Spanish text of the plain ``data-i18n`` elements."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.keys: set[str] = set()
        self.texts: dict[str, set[str]] = {}
        self.loose: set[str] = set()  # text outside any i18n element, svg, script or style
        self._open: list[tuple[str, str | None, list[str]]] = []
        self._covered: list[bool] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        for name in ("data-i18n", "data-i18n-html", "data-i18n-aria", "data-i18n-content"):
            if values.get(name):
                self.keys.add(values[name])  # type: ignore[arg-type]
        if values.get("data-i18n-aria"):
            self.texts.setdefault(values["data-i18n-aria"], set()).add(values.get("aria-label") or "")  # type: ignore[index]
        if values.get("data-i18n-content"):
            self.texts.setdefault(values["data-i18n-content"], set()).add(values.get("content") or "")  # type: ignore[index]
        self._open.append((tag, values.get("data-i18n"), []))
        inherited = bool(self._covered and self._covered[-1])
        covered = tag in ("svg", "script", "style", "title") or "data-i18n" in values or "data-i18n-html" in values
        covered = covered or values.get("translate") == "no"  # code: its comments carry their own data-i18n
        self._covered.append(inherited or covered)

    def handle_data(self, data: str) -> None:
        if data.strip() and not (self._covered and self._covered[-1]):
            self.loose.add(data.strip())
        for _, _, chunks in self._open:
            chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        while self._open:
            open_tag, key, chunks = self._open.pop()
            self._covered.pop()
            if key:
                self.texts.setdefault(key, set()).add("".join(chunks))
            if open_tag == tag:
                break


def _strings(page: str) -> dict[str, dict[str, str]]:
    return json.loads((ROOT / "shared" / "i18n" / f"{page}.json").read_text(encoding="utf-8"))


# shared/i18n.js UNIT_WORDS: how each system writes the units a text names as {u_len}, {u_moment}...
UNIT_WORDS = {
    "si": {"len": "cm", "cov": "mm", "span": "cm", "force": "kN", "moment": "kNm"},
    "us": {"len": "in", "cov": "in", "span": "ft", "force": "kip", "moment": "kip-ft"},
}


def _resolve(strings: dict[str, str], units: str) -> dict[str, str]:
    """shared/i18n.js resolveStrings: the US variants in, the unit tokens filled."""

    def pick(key: str) -> str:
        return strings[f"{key}_us"] if units == "us" and f"{key}_us" in strings else strings[key]

    def fill(text: str) -> str:
        return re.sub(r"\{u_(\w+)\}", lambda m: UNIT_WORDS[units].get(m[1]) or pick(f"u_{m[1]}"), text)

    return {
        key: fill(pick(key)) if isinstance(pick(key), str) else pick(key) for key in strings if not key.endswith("_us")
    }


def _parse(page: str) -> _Strings:
    parser = _Strings()
    parser.feed(PAGES[page].read_text(encoding="utf-8"))
    return parser


@pytest.mark.parametrize("page", PAGES)
def test_every_key_is_translated(page):
    strings, parsed = _strings(page), _parse(page)
    for lang in LANGS:
        assert parsed.keys <= set(strings[lang]), f"{lang} lacks {sorted(parsed.keys - set(strings[lang]))}"
    assert set(strings["es"]) == set(strings["en"])


@pytest.mark.parametrize("page", PAGES)
def test_first_paint_is_the_spanish_of_the_dictionary(page):
    es, parsed = _resolve(_strings(page)["es"], "si"), _parse(page)
    for key, texts in parsed.texts.items():
        assert texts == {es[key]}, key


@pytest.mark.parametrize("page", PAGES)
def test_no_ui_text_outside_the_dictionary(page):
    """What is not translated must be a name, a number or a symbol, never copy."""
    names = {"mento", "/", "ES", "EN", "GitHub", "LinkedIn", "Me lo dijo un ingeniero", "RAMÉ Ingeniería", "·"}
    names |= {
        "SI",
        "US",
        "1",
        "2",
        "3",
        "4",
        "ACI 318-19 · CIRSOC 201-25 · EN 1992",
    }
    # API names (pip install mento, node.check()) are code, not copy: the home's Python section
    # shows them exactly as mento spells them. The calculators no longer do; they are for the end user.
    api = re.compile(r"(node|beam|slab|wall)(_1)?\.\w+(\(\))?|pip install mento")
    beams = re.compile(r"V\d{3}")  # element labels, as in an Excel of beams
    loose = {
        text
        for text in _parse(page).loose
        if text not in names and not NOTATION.fullmatch(text) and not api.fullmatch(text) and not beams.fullmatch(text)
    }
    assert loose == set()


def test_home_names_the_mento_that_runs():
    worker = (ROOT / "shared" / "worker.js").read_text(encoding="utf-8")
    pinned = re.search(r'MENTO_VERSION = "([^"]+)"', worker).group(1)  # type: ignore[union-attr]
    assert f"mento=={pinned}" in (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    # every page, not only the home: a calculator shows a saved result only if that mento computed it
    for page in PAGES.values():
        assert f'<span id="version">{pinned}</span>' in page.read_text(encoding="utf-8"), page


def _main(section: dict) -> list[dict]:
    """The bars the checks count: not the mounting bars, nor the skin."""
    return [bar for bar in section["bars"] if bar["kind"] == "main"]


def test_hero_shows_the_worked_example():
    """The hero is built from real components: when mento or the example changes, so must it."""
    result = json.loads(beam.run(json.dumps(beam.EXAMPLE)))
    assert result["ok"], result
    source = PAGES["home"].read_text(encoding="utf-8")
    hero = dict(re.findall(r'data-hero="([a-z0-9_]+)">([^<]*)<', source))
    bottom, top, shear = result["flexure"]["bottom"], result["flexure"]["top"], result["shear"]
    assert hero["bottom"] == hero["opt1"] == bottom["bars"]
    assert hero["top"] == top["bars"]
    assert hero["stirrups"] == shear["stirrups"] == "1eØ6/28cm"  # one closed stirrup, as mento writes it
    assert hero["skin"] == result["skin"]["bars"]  # 60 cm deep: mento lays skin out on each side face
    assert (hero["width"], hero["height"]) == (f"{beam.EXAMPLE['width']} cm", f"{beam.EXAMPLE['height']} cm")
    assert hero["bottom_dcr"] == hero["opt1_dcr"] == f"{bottom['DCR']:.2f}"
    assert hero["top_dcr"] == f"{top['DCR']:.2f}"
    assert hero["shear_dcr"] == f"{shear['DCR']:.2f}"
    governing = max(bottom["DCR"], top["DCR"], shear["DCR"])
    assert hero["dcr"] == f"{governing:.2f}"
    assert governing == bottom["DCR"] <= 0.9  # the bottom flexure governs: picking an option moves the verdict
    assert hero["sum_flex"] == f"{max(bottom['DCR'], top['DCR']):.2f}" and hero["sum_shear"] == hero["shear_dcr"]


def test_hero_options_are_what_mento_proposes():
    """Each option the hero lets you pick, with what home.js redraws and links from it."""
    result = json.loads(beam.run(json.dumps(beam.EXAMPLE)))
    source = PAGES["home"].read_text(encoding="utf-8")
    hero = dict(re.findall(r'data-hero="([a-z0-9_]+)">([^<]*)<', source))
    viz = re.search(r'<div class="viz" id="hero" data-top="([^"]+)" data-st="([^"]+)">', source)
    assert viz and (viz[1], viz[2]) == (signature(result["layouts"]["top"]), signature(result["layouts"]["st"]))
    options = re.findall(
        r'<label class="opt" data-sig="([^"]+)" data-mid="([^"]+)">.*?<span class="area">([^<]*)<', source
    )
    assert len(options) == len(result["options"]["bot"]) >= 2  # as many as mento proposes, and a choice
    for index, ((sig, mid, area), option) in enumerate(zip(options, result["options"]["bot"], strict=True), start=1):
        assert (sig, area) == (option["signature"], option["area"])
        assert hero[f"opt{index}"] == option["bars"] and hero[f"opt{index}_dcr"] == f"{option['dcr']:.2f}"
        picked = json.loads(beam.run(json.dumps({**beam.EXAMPLE, "choice": {"bot": sig}})))
        # home.js moves only the bottom flexure: the top and the shear must not change with it
        ledger = {row["key"]: f"{row['dcr']:.2f}" for row in picked["ledger"]}
        assert ledger == {
            "flexure_bottom": hero[f"opt{index}_dcr"],
            "flexure_top": hero["top_dcr"],
            "shear": hero["shear_dcr"],
        }
        # and it governs in every option, so the verdict moves with the pick
        assert max(row["dcr"] for row in picked["ledger"]) == picked["ledger"][0]["dcr"]
        low = [bar for bar in _main(picked["section"]) if bar["y"] < beam.EXAMPLE["height"] / 2]
        corners = {min(bar["x"] for bar in low), max(bar["x"] for bar in low)}
        between = [f"{bar['x']:g},{bar['y']:g},{bar['d']:g}" for bar in low if bar["x"] not in corners]
        assert mid.split(";") == between


def test_the_us_hero_is_the_us_example():
    """What home.js draws for a visitor in US units: beam/'s US example, option by option, as mento gives it."""
    source = PAGES["home"].read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script type="application/json" id="hero-us">(.*?)</script>', source, re.DOTALL)[1])
    result = json.loads(beam.run(json.dumps(beam.EXAMPLE_US)))
    section = result["section"]
    assert (data["width"], data["height"]) == (section["width"], section["height"])
    assert data["stirrup"] == section["stirrup"] and [data["closed"]] == section["closed"]
    assert data["bars"] == [[bar["x"], bar["y"], bar["d"]] for bar in _main(section)]
    skin = [bar for bar in section["bars"] if bar["kind"] == "skin"]
    assert (
        data["skin_bars"] == [[bar["x"], bar["y"], bar["d"]] for bar in skin] and data["skin"] == result["skin"]["bars"]
    )
    assert len(section["bars"]) == len(data["bars"]) + len(data["skin_bars"])  # and no mounting bar to draw
    assert (data["top"], data["stirrups"]) == (result["rebar"]["top"], result["rebar"]["st"])
    assert (data["data_top"], data["data_st"]) == (
        signature(result["layouts"]["top"]),
        signature(result["layouts"]["st"]),
    )
    ledger = {row["key"]: f"{row['dcr']:.2f}" for row in result["ledger"]}
    assert (data["top_dcr"], data["shear_dcr"]) == (ledger["flexure_top"], ledger["shear"])
    assert len(data["options"]) == len(result["options"]["bot"]) >= 2  # home.js makes an option of each
    for shown, option in zip(data["options"], result["options"]["bot"], strict=True):
        assert (shown["sig"], shown["bars"], shown["area"]) == (option["signature"], option["bars"], option["area"])
        assert shown["dcr"] == f"{option['dcr']:.2f}"
        picked = json.loads(beam.run(json.dumps({**beam.EXAMPLE_US, "choice": {"bot": shown["sig"]}})))
        moved = {row["key"]: f"{row['dcr']:.2f}" for row in picked["ledger"]}
        assert moved == {"flexure_bottom": shown["dcr"], "flexure_top": data["top_dcr"], "shear": data["shear_dcr"]}
        assert max(row["dcr"] for row in picked["ledger"]) == picked["ledger"][0]["dcr"]
        low = [bar for bar in _main(picked["section"]) if bar["y"] < section["height"] / 2]
        assert shown["low"].split(";") == [f"{bar['x']:g},{bar['y']:g},{bar['d']:g}" for bar in low]


# The four beams of mento's example notebook (beam_summary_ACI_318-19), as BeamSummary reads them:
# the sections and the forces, each with its units row first as the sheets of the Excel carry it.
GUIDE_SECTIONS = {
    "Label": ["", "V101", "V102", "V103", "V104"],
    "b": ["cm", 20, 20, 20, 20],
    "h": ["cm", 50, 50, 50, 50],
    "cc": ["mm", 25, 25, 25, 25],
    "legs": ["", 0, 2, 2, 2],
    "dbs": ["mm", 0, 10, 10, 10],
    "sl": ["cm", 0, 20, 20, 20],
    "n1_top": ["", 2, 2, 2, 2],
    "db1_top": ["mm", 8, 12, 8, 8],
    "n2_top": ["", 0, 1, 0, 0],
    "db2_top": ["mm", 0, 16, 0, 0],
    "n1_bot": ["", 2, 2, 2, 2],
    "db1_bot": ["mm", 12, 8, 12, 12],
    "n2_bot": ["", 1, 0, 1, 0],
    "db2_bot": ["mm", 10, 0, 10, 0],
    "n3_bot": ["", 2, 0, 2, 0],
    "db3_bot": ["mm", 12, 0, 16, 0],
}
GUIDE_FORCES = {
    "Label": ["", "V101", "V102", "V103", "V104"],
    "Comb.": ["", "ELU 1", "ELU 2", "ELU 3", "ELU 4"],
    "Nx": ["kN", 0, 0, 0, 0],
    "Vz": ["kN", 20, -50, 100, 100],
    "My": ["kNm", 0, -35, 40, 45],
}


def test_python_section_shows_what_beam_summary_returns():
    """The table under the notebook: the governing DCR of each beam with its rebar, then after design(),
    each with the verdict mento gives it (a section that misses a limit fails, whatever its DCR)."""
    conc = Concrete_ACI_318_19(name="H-25", f_c=25 * MPa)
    steel = SteelBar(name="ADN 420", f_y=420 * MPa)
    summary = BeamSummary(conc, steel, pd.DataFrame(GUIDE_SECTIONS), pd.DataFrame(GUIDE_FORCES))

    def governing() -> dict[str, tuple[str, bool]]:
        rows = summary.check().iloc[1:]  # the first row holds the units
        dcrs = {row["Beam"]: max(row["DCRb,top"], row["DCRb,bot"], row["DCRv"]) for _, row in rows.iterrows()}
        passes = {verdict.key[1]: verdict.passes for verdict in summary.results}
        return {label: (f"{dcr:.2f}", passes[label]) for label, dcr in dcrs.items()}

    # the glue leaves mento in the language of the last report; the columns are read in English
    previous = mento.get_language()
    mento.set_language("en")
    try:
        checked = governing()
        summary.design()
        designed = governing()
    finally:
        mento.set_language(previous)
    source = PAGES["home"].read_text(encoding="utf-8")
    shown = {
        cell: (dcr, state == "ok")
        for state, cell, dcr in re.findall(r'<td class="dcr (ok|bad)"><span data-sum="(V\d+ \w+)">([^<]*)<', source)
    }
    expected = {f"{label} check": verdict for label, verdict in checked.items()}
    expected |= {f"{label} design": verdict for label, verdict in designed.items()}
    assert shown == expected
    # the story the table tells: a beam fails as drawn, all pass once mento designs them
    assert sorted(label for label, (_, passes) in checked.items() if not passes) == ["V104"]
    assert all(passes for _, passes in designed.values())
    # and the snippet above it reads them the way mento now does: two sheets, sections and forces
    assert "BeamSummary.from_excel(conc, steel," in re.sub(r"<[^>]+>", "", source)


@pytest.mark.parametrize("page", ["slab", "wall"])
def test_every_calculator_has_the_beams_forces_block(page):
    """One forces editor, the same everywhere: only the hint under it may say what applies."""

    def station_2(name: str) -> str:
        source = PAGES[name].read_text(encoding="utf-8")
        block = re.search(r'<section class="station station-2">.*?</section>', source, re.DOTALL).group(0)  # type: ignore[union-attr]
        return re.sub(r'(data-i18n-html="hint2">).*?(</p>)', r"\1\2", block, flags=re.DOTALL)

    assert station_2(page) == station_2("beam")
    app = (ROOT / page / "app.js").read_text(encoding="utf-8")
    assert 'forces: ["M_y", "V_z", "N_x"],' in app


def test_stats_json_is_either_published_or_empty():
    """The home reads the test count from here. Until mento's release workflow publishes it, it holds
    null and the band shows no figure: a number is only ever the one CI wrote (stats.yml)."""
    tests = json.loads((ROOT / "shared" / "stats.json").read_text(encoding="utf-8"))["tests"]
    assert tests is None or (isinstance(tests, int) and tests > 0)
