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
# "2Ø16 + 1Ø12", the symbols the design system uses as field labels (M, V, c/, eØ) and units.
NOTATION = re.compile(r"[\d.]+( cm²)?|\d+Ø\d+( \+ \d+Ø\d+)*|[A-Za-zØ+×·/−]{1,3}|cm|mm|MPa|kN|kNm|cm²|· mento")


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
    es, parsed = _strings(page)["es"], _parse(page)
    for key, texts in parsed.texts.items():
        assert texts == {es[key]}, key


@pytest.mark.parametrize("page", PAGES)
def test_no_ui_text_outside_the_dictionary(page):
    """What is not translated must be a name, a number or a symbol, never copy."""
    names = {"mento", "/", "ES", "EN", "GitHub", "LinkedIn", "Me lo dijo un ingeniero", "RAMÉ Ingeniería", "·"}
    names |= {
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
    assert f'<span id="version">{pinned}</span>' in PAGES["home"].read_text(encoding="utf-8")


def test_hero_shows_the_worked_example():
    """The hero is built from real components: when mento or the example changes, so must it."""
    result = json.loads(beam.run(json.dumps(beam.EXAMPLE)))
    assert result["ok"], result
    source = PAGES["home"].read_text(encoding="utf-8")
    hero = dict(re.findall(r'data-hero="([a-z0-9_]+)">([^<]*)<', source))
    bottom, top, shear = result["flexure"]["bottom"], result["flexure"]["top"], result["shear"]
    assert hero["bottom"] == hero["opt1"] == bottom["bars"]
    assert hero["top"] == top["bars"]
    assert hero["stirrups"] == shear["stirrups"].replace(" cm", "").replace("/", " c/")
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
    assert len(options) == len(result["options"]["bot"]) == 2
    for index, ((sig, mid, area), option) in enumerate(zip(options, result["options"]["bot"]), start=1):
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
        low = [bar for bar in picked["section"]["bars"] if bar["y"] < beam.EXAMPLE["height"] / 2]
        corners = {min(bar["x"] for bar in low), max(bar["x"] for bar in low)}
        between = [f"{bar['x']:g},{bar['y']:g},{bar['d']:g}" for bar in low if bar["x"] not in corners]
        assert mid.split(";") == between


# The four beams of the home's example as BeamSummary reads them: the sheets Sections and Forces of
# project.xlsx, a units row first as the Excel carries it. Groups mento does not need are left out.
SECTIONS = {
    "Label": ["", "V101", "V102", "V103", "V104"],
    "b": ["cm", 20, 20, 20, 20],
    "h": ["cm", 50, 50, 50, 50],
    "cc": ["mm", 25, 25, 25, 25],
    "legs": ["", 0, 2, 2, 2],
    "dbs": ["mm", 0, 6, 6, 6],
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
FORCES = {
    "Label": ["", "V101", "V102", "V103", "V104"],
    "Comb.": ["", "ELU 1", "ELU 2", "ELU 3", "ELU 4"],
    "Nx": ["kN", 0, 0, 0, 0],
    "Vz": ["kN", 20, -50, 100, 100],
    "My": ["kNm", 0, -35, 40, 45],
}


def test_python_section_shows_what_beam_summary_returns():
    """The table under the notebook: the governing DCR of each beam with its rebar, then after design()."""
    conc = Concrete_ACI_318_19(name="H-25", f_c=25 * MPa)
    steel = SteelBar(name="ADN 420", f_y=420 * MPa)
    summary = BeamSummary(conc, steel, pd.DataFrame(SECTIONS), pd.DataFrame(FORCES))

    def governing() -> tuple[dict[str, float], set[str]]:
        """Each beam's largest DCR, and the beams mento itself passes: a mark on the page is its ``Ok?``."""
        rows = summary.check().iloc[1:]  # the first row holds the units
        dcrs = {row["Beam"]: max(row["DCRb,top"], row["DCRb,bot"], row["DCRv"]) for _, row in rows.iterrows()}
        return dcrs, {row["Beam"] for _, row in rows.iterrows() if row["Ok?"] == "✅"}

    # the glue leaves mento in the language of the last report; the columns are read in English
    previous = mento.get_language()
    mento.set_language("en")
    try:
        checked, checked_ok = governing()
        summary.design()
        designed, designed_ok = governing()
    finally:
        mento.set_language(previous)
    source = PAGES["home"].read_text(encoding="utf-8")
    shown = dict(re.findall(r'data-sum="(V\d+ \w+)">([^<]*)<', source))
    expected = {f"{label} check": f"{dcr:.2f}" for label, dcr in checked.items()}
    expected |= {f"{label} design": f"{dcr:.2f}" for label, dcr in designed.items()}
    assert shown == expected
    # the story the table tells: two beams fail as drawn, all pass once mento designs them
    assert checked_ok == {"V101", "V102"} and designed_ok == set(designed)
    assert sorted(label for label, dcr in checked.items() if dcr > 1) == ["V103", "V104"]
    assert max(designed.values()) <= 1
    assert "BeamSummary.from_excel(" in source  # the single table is deprecated, mento 2.0 will not read it


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
