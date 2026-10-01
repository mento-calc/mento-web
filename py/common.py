"""What the three calculators share: materials, forces, tables, options and the Word report.

Runs inside Pyodide in the browser, but has no Pyodide-specific code, so every calculator can
be exercised from a normal interpreter.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import io
import json
import math
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from mento import (
    Concrete_ACI_318_19,
    Concrete_CIRSOC_201_25,
    Concrete_EN_1992_2004,
    Forces,
    SteelBar,
    bar_designation,
    bar_diameter,
    ureg,
)
from mento.verification import warning_category

CONCRETES = {
    "ACI 318-19": Concrete_ACI_318_19,
    "CIRSOC 201-25": Concrete_CIRSOC_201_25,
    "EN 1992-2004": Concrete_EN_1992_2004,
}
# ØMn under ACI and CIRSOC, MRd under EN: the symbol belongs to the code the user picked.
CAPACITY = {"EN 1992-2004": {"M": "MRd", "V": "VRd"}}
DEFAULT_CAPACITY = {"M": "ØMn", "V": "ØVn"}


class InputError(ValueError):
    """A problem with what the user typed, reported back by field name."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def number(data: dict[str, Any], key: str, *, positive: bool = True) -> float:
    try:
        value = float(data.get(key))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise InputError(key, "missing") from None
    if math.isnan(value) or (positive and value <= 0):
        raise InputError(key, "positive")
    return value


def magnitude(value: Any, unit: str, precision: int = 1) -> float | None:
    return None if value is None else round(float(value.to(unit).magnitude), precision)


# ------------------------------------------------------------------------ unit systems
# A US bar is named by its ASTM A615 size, as mento names it: bar_diameter(6) is 0.75 in and
# bar_designation(0.75 in) is "#6". The table is mento's (mento.bar_sizes), never copied here.


@dataclass(frozen=True)
class Units:
    """What the page's numbers mean in one system, and how it writes a bar.

    A layout names its bars by what a drawing calls them: the diameter in mm in "si" (16 is Ø16),
    the ASTM size in "us" (6 is #6). Spacings, and every length the page draws, are in ``length``.
    """

    name: str
    length: str  # section sizes, spacings and the drawing
    cover: str
    span: str  # a wall's length and height
    f_c: str
    f_y: str
    force: str
    moment: str
    labels: dict[str, str]  # how each kind of quantity is written
    run: float  # spacing units in the length a per-length area counts: 100 cm in a m, 12 in in a ft
    diameters: tuple[int, ...]  # the bars the page offers as alternatives
    min_spacing: float  # the tightest mesh the page offers

    @property
    def us(self) -> bool:
        return self.name == "us"

    def bar(self, d: float) -> Any:
        """A layout's bar as mento takes it: a diameter."""
        if not self.us:
            return d * ureg.mm
        try:
            if int(d) != d:
                raise ValueError(d)
            return bar_diameter(int(d))
        except ValueError:
            raise InputError("rebar", "bar_size") from None

    def bar_of(self, d_b: Any) -> float:
        """What a layout calls a bar mento placed: its diameter in mm, or its ASTM size."""
        if not self.us:
            return round(float(d_b.to("mm").magnitude), 3)
        name = bar_designation(d_b)
        if not name.startswith("#"):  # mento writes a diameter no ASTM size has as Ø0.70"
            raise ValueError(f"a bar of {name} is no ASTM size")
        return int(name[1:])

    def size(self, d: float) -> float:
        """A layout's bar diameter in ``length``, for the drawing."""
        return round(float(self.bar(d).to(self.length).magnitude), 4)

    def bar_area(self, d: float) -> float:
        return math.pi * self.size(d) ** 2 / 4

    def bar_name(self, d: float) -> str:
        return f"#{d:g}" if self.us else f"Ø{d:g}"

    def show(self, value: Any, kind: str, precision: int = 2) -> str | None:
        """A quantity as the page writes it: ``8.55 cm²``, ``1.32 in²``, ``123.9 kip-ft``."""
        if value is None:
            return None
        unit = {"area": self.labels["area_unit"], "per_length": self.labels["per_length_unit"]}.get(kind)
        return f"{float(value.to(unit or getattr(self, kind)).magnitude):.{precision}f} {self.labels[kind]}"


SI = Units(
    name="si",
    length="cm",
    cover="mm",
    span="cm",
    f_c="MPa",
    f_y="MPa",
    force="kN",
    moment="kN*m",
    labels={
        "length": "cm",
        "area": "cm²",
        "area_unit": "cm**2",
        "per_length": "cm²/m",
        "per_length_unit": "cm**2/m",
        "force": "kN",
        "moment": "kN·m",
        "ledger_moment": "kNm",
    },
    run=100.0,
    diameters=(6, 8, 10, 12, 16, 20, 25),
    min_spacing=10.0,
)
US = Units(
    name="us",
    length="inch",
    cover="inch",
    span="ft",
    f_c="psi",
    f_y="ksi",
    force="kip",
    moment="kip*ft",
    labels={
        "length": "in",
        "area": "in²",
        "area_unit": "inch**2",
        "per_length": "in²/ft",
        "per_length_unit": "inch**2/ft",
        "force": "kip",
        "moment": "kip-ft",
        "ledger_moment": "kip-ft",
    },
    run=12.0,
    diameters=(3, 4, 5, 6, 7, 8),
    min_spacing=4.0,
)
SYSTEMS = {"si": SI, "us": US}
# mento reads US customary off f'c in psi, and only ACI 318-19 is written for it.
US_CODES = ("ACI 318-19",)


def units_of(data: dict[str, Any]) -> Units:
    """The system the page speaks: "si" when a payload names none, as every link from before."""
    name = data.get("units") or "si"
    if name not in SYSTEMS:
        raise InputError("units", "unknown")
    return SYSTEMS[name]


def materials(data: dict[str, Any]) -> tuple[Any, SteelBar]:
    """f'c and fy, in MPa, or in psi and ksi (a US grade is its fy in ksi: Grade 60)."""
    code = data.get("code")
    if code not in CONCRETES:
        raise InputError("code", "unknown")
    units = units_of(data)
    if units.us and code not in US_CODES:
        raise InputError("code", "units")
    f_c = number(data, "fc")
    f_y = number(data, "fy")
    concrete = CONCRETES[code](name=f"f'c {f_c:g}", f_c=f_c * ureg(units.f_c))
    return concrete, SteelBar(name=f"fy {f_y:g}", f_y=f_y * ureg(units.f_y))


def build_forces(data: dict[str, Any]) -> list[Forces]:
    """Factored forces in kN and kNm, or in kip and kip-ft."""
    units = units_of(data)
    force, moment = ureg(units.force), ureg(units.moment)
    system = "imperial" if units.us else "metric"
    forces = []
    for index, row in enumerate(data.get("forces") or []):
        m_y = float(row.get("M_y") or 0)
        v_z = float(row.get("V_z") or 0)
        n_x = float(row.get("N_x") or 0)
        if m_y == 0 and v_z == 0 and n_x == 0:
            continue
        label = str(row.get("label") or f"C{index + 1}")
        forces.append(Forces(label=label, M_y=m_y * moment, V_z=v_z * force, N_x=n_x * force, unit_system=system))
    if not forces:
        raise InputError("forces", "empty")
    return forces


def label_of(data: dict[str, Any], default: str) -> str:
    return str(data.get("label") or default)


# ------------------------------------------------------------------------ tables and text


def cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    if isinstance(value, bool):
        return "✓" if value else "✕"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def table(frame: Any) -> dict[str, Any]:
    """mento's DataFrame as it prints it: same columns, same order, its units row in the header."""
    columns = [str(name) for name in frame.columns]
    rows = [[cell(value) for value in row] for row in frame.itertuples(index=False)]
    # mento puts the units in the first row, under an empty label
    units = rows.pop(0) if rows and not rows[0][0].strip() else [""] * len(columns)
    return {"columns": columns, "units": units, "rows": rows, "dcr": columns.index("DCR") if "DCR" in columns else None}


def printed(*calls: Callable[[], Any]) -> str:
    """What mento prints, captured: it writes its detailed results to stdout."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for write in calls:
            write()
    return out.getvalue()


def detailed(*calls: Callable[[], Any]) -> dict[str, Any]:
    """The detailed results twice: as mento prints them, for the Copiá button, and as tables."""
    text = printed(*calls)
    return {"detailed": text, "reports": reports(text)}


_BANNER = re.compile(r"=+ (.+?) =+")
_RULE = re.compile(r"-+(?: +-+)*")


def reports(text: str) -> list[dict[str, Any]]:
    """What mento prints as detailed results, as data the page lays out itself.

    mento keeps the tables behind those printouts private, so this reads the printout: one report
    per ``===== TITLE =====`` banner, one table per block. A block is a header line, a rule of
    dashes that marks where each column starts, rows, and a blank line. The first header cell names
    the table and the first column describes each row. A cell runs to where the next column starts,
    not to the end of its dashes: a value wider than its column (an emoji counted twice) stays whole.
    """
    found: list[dict[str, Any]] = []
    lines = [line.rstrip() for line in text.splitlines()]
    index = 0
    while index < len(lines):
        banner = _BANNER.fullmatch(lines[index].strip())
        if banner:
            found.append({"title": banner.group(1), "tables": []})
            index += 1
            continue
        if found and index + 1 < len(lines) and lines[index].strip() and _RULE.fullmatch(lines[index + 1].strip()):
            starts = [match.start() for match in re.finditer(r"-+", lines[index + 1])]

            def cut(line: str, starts: list[int] = starts) -> list[str]:
                ends = [*starts[1:], None]
                return [line[start:end].strip() for start, end in zip(starts, ends, strict=True)]

            header = cut(lines[index])
            index += 2
            rows = []
            while index < len(lines) and lines[index].strip() and not _BANNER.fullmatch(lines[index].strip()):
                rows.append(cut(lines[index]))
                index += 1
            found[-1]["tables"].append({"title": header[0], "columns": header[1:], "rows": rows})
            continue
        index += 1
    return found


# ------------------------------------------------------------------------ design options


def select(options: dict[str, list[dict[str, Any]]], choice: dict[str, Any]) -> tuple[dict[str, int], list[str]]:
    """Keep the option the user picked if the new proposal still has it; otherwise fall back to
    mento's own and say which groups changed, so the page can flag them (5.4)."""
    selected: dict[str, int] = {}
    changed: list[str] = []
    for group, group_options in options.items():
        signatures = [option["signature"] for option in group_options]
        wanted = choice.get(group)
        if wanted in signatures:
            selected[group] = signatures.index(wanted)
        else:
            selected[group] = 0
            if wanted:
                changed.append(group)
    return selected, changed


def selected_layout(options: dict[str, Any], group: str, selected: dict[str, int]) -> dict[str, Any]:
    group_options = options.get(group) or [{}]
    return group_options[min(selected.get(group, 0), len(group_options) - 1)].get("layout", {})


def fill_option_dcrs(
    options: dict[str, Any],
    selected: dict[str, int],
    current: dict[str, tuple[float | None, bool | None]],
    evaluate: Callable[[dict[str, Any], str], tuple[float | None, bool | None]],
) -> None:
    """The DCR beside an option, and whether its group complies, are that option's own with the
    rest of the section as the user has it. mento's ``section_DCR`` is the worst of the whole
    section with the rest as the design applied it: the same for most options, and blind to a
    choice made in another group."""
    for group, group_options in options.items():
        for index, option in enumerate(group_options):
            if index == selected.get(group, 0):
                option["dcr"], option["complies"] = current[group]
                continue
            layouts = {name: selected_layout(options, name, selected) for name in options}
            layouts[group] = option["layout"]
            option["dcr"], option["complies"] = evaluate(layouts, group)


def spacing_options(
    area_required: float,
    chosen: dict[str, Any],
    limit: int,
    max_spacing: float,
    units: Units,
) -> list[dict[str, Any]]:
    """Mesh layouts of one bar at a spacing, mento's own pick first.

    mento offers no alternatives for a wall's mesh, so they are built here: for each bar, the widest
    whole-centimetre (or whole-inch) spacing that still provides ``area_required`` (cm²/m or
    in²/ft), capped by the code's limit. Every one of them is then checked by mento like any other
    reinforcement.
    """
    layouts = [chosen] if chosen else []
    # One alternative per bar: the same bar at a slightly different spacing is not a choice.
    seen_diameters = {(chosen or {}).get("d")}
    for diameter in units.diameters:
        if diameter in seen_diameters or area_required <= 0:
            continue
        spacing = math.floor(min(units.bar_area(diameter) / area_required * units.run, max_spacing))
        if spacing < units.min_spacing:
            continue
        seen_diameters.add(diameter)
        layouts.append({"d": float(diameter), "s": float(spacing)})
    middle = units.diameters[2]
    ordered = layouts[:1] + sorted(layouts[1:], key=lambda item: abs(item["d"] - (chosen or {}).get("d", middle)))
    return ordered[:limit]


def signature(layout: dict[str, Any]) -> str:
    """Short, stable name of a layout: what the URL keeps and what re-picks it after a recalc."""
    if "s" in layout and "n" in layout:  # stirrups: legs, diameter and spacing
        return f"{layout['n']:g}x{layout['d']:g}@{layout['s']:g}"
    if "s" in layout:  # a mesh: diameter at a spacing
        return f"{layout['d']:g}@{layout['s']:g}"
    return "+".join(f"{layout[f'n{i}']:g}x{layout[f'd{i}']:g}" for i in range(1, 5) if layout.get(f"n{i}"))


def mesh_bars(layout: dict[str, Any], units: Units) -> str:
    """``Ø10c/15cm``, or ``#4@12in`` as a US drawing calls it: compact, as a drawing labels it."""
    if not layout:
        return ""
    if units.us:
        return f"{units.bar_name(layout['d'])}@{layout['s']:g}in"
    return f"{units.bar_name(layout['d'])}c/{layout['s']:g}cm"


def mesh_area(layout: dict[str, Any], units: Units, curtains: int = 1) -> str:
    """The steel a mesh puts in, per metre or per foot. A wall carries one curtain on each face."""
    if not layout:
        return ""
    return f"{curtains * units.bar_area(layout['d']) * units.run / layout['s']:.2f} {units.labels['per_length']}"


# ------------------------------------------------------------------------ verdict and notices


def captured_notices(captured: list[Any]) -> list[dict[str, Any]]:
    """Every Python warning mento raised while checking, as it worded it."""
    return [{"code": "mento", "severity": "warn", "values": {"message": str(item.message)}} for item in captured]


# What each of mento's warning categories (``mento.verification.warning_category``) is to the page.
# A strength the section lacks ("resistance") or a limit of the code it misses ("failed": spacing,
# bars that do not fit, a cage that cannot be detailed) fails the element, whatever its DCR. A check
# mento leaves pending is flagged and fails nothing, and a note only informs (the skin a deep beam
# requires, which mento lays out). A code mento has not classed is pending.
SEVERITY = {"resistance": "bad", "failed": "bad", "pending": "warn", "informative": "info"}


def warning_notices(element: Any, captured: list[Any]) -> list[dict[str, Any]]:
    """What the page flags, as mento judges it: its Python warnings, then its ``DesignWarning``s.

    ``severity`` is the page's reading of mento's category (``SEVERITY``): only "bad" fails the
    element. ``code`` is mento's stable one; the page shows ``message``, which mento writes in the
    language set with ``mento.set_language``."""
    notices = captured_notices(captured)
    for warning in element.warnings:
        notices.append(
            {
                "code": warning.code,
                "severity": SEVERITY.get(warning_category(warning.code), "warn"),
                "message": warning.message,
                "values": {
                    "face": warning.face,
                    "combos": list(warning.combinations),
                    **({"direction": warning.values["direction"]} if "direction" in warning.values else {}),
                },
            }
        )
    return notices


def flexure_rows(element: Any, forces: list[Any], code: str, units: Units) -> list[dict[str, Any]]:
    """The bottom and top flexure rows of the ledger, from the last check.

    Each face shows the combination with its largest DCR, but a face complies only if every
    combination leaves it compliant: under ACI 318-19 and CIRSOC 201-25 a section past the
    tension-controlled limit does not, whatever its DCR (``FlexureFaceCheck.complies``)."""
    symbols = CAPACITY.get(code, DEFAULT_CAPACITY)
    demands = {force.label: force for force in forces}
    checks = element.flexure_checks
    rows = []
    for key, name in (("flexure_bottom", "bottom"), ("flexure_top", "top")):
        governing = max(checks, key=lambda check, name=name: getattr(check, name).DCR)
        check = getattr(governing, name)
        moment = float(demands[governing.label].M_y.to(units.moment).magnitude)
        loaded = moment > 0 if name == "bottom" else moment < 0
        rows.append(
            {
                "key": key,
                "dcr": round(float(check.DCR), 3) if loaded else None,
                "complies": all(getattr(each, name).complies for each in checks),
                "symbol": symbols["M"],
                "demand_symbol": "Mu",
                "capacity": magnitude(check.M_capacity, units.moment),
                "demand": round(abs(moment), 1) if loaded else None,
                "unit": units.labels["ledger_moment"],
                "combo": governing.label if loaded else None,
            }
        )
    return rows


def shear_row(element: Any, forces: list[Any], code: str, units: Units) -> dict[str, Any]:
    symbols = CAPACITY.get(code, DEFAULT_CAPACITY)
    demands = {force.label: force for force in forces}
    shear = max(element.shear_checks, key=lambda check: check.DCR)
    return {
        "key": "shear",
        "dcr": round(float(shear.DCR), 3),
        "symbol": symbols["V"],
        "demand_symbol": "Vu",
        "capacity": magnitude(shear.V_capacity, units.force),
        "demand": round(abs(float(demands[shear.label].V_z.to(units.force).magnitude)), 1),
        "unit": units.labels["force"],
        "combo": shear.label,
    }


# ------------------------------------------------------------------------ the Word report


def _is_empty_paragraph(element: Any) -> bool:
    return (
        element.tag == qn("w:p")
        and not any((node.text or "").strip() for node in element.iter(qn("w:t")))
        and not any(True for _ in element.iter(qn("w:drawing")))
    )


def _starts_new_page(paragraph: Any) -> None:
    """Mark a paragraph to start a page, in the place the schema wants it inside w:pPr."""
    properties = paragraph.find(qn("w:pPr"))
    if properties is None:
        properties = OxmlElement("w:pPr")
        paragraph.insert(0, properties)
    if properties.find(qn("w:pageBreakBefore")) is not None:
        return
    # w:pageBreakBefore goes after w:pStyle, w:keepNext and w:keepLines, before everything else
    position = sum(1 for child in properties if child.tag in {qn("w:pStyle"), qn("w:keepNext"), qn("w:keepLines")})
    properties.insert(position, OxmlElement("w:pageBreakBefore"))


def merge_documents(paths: list[str]) -> bytes:
    """One Word file out of several: each extra document starts on a new page of the first.

    mento ends each document with a table and an empty paragraph after it. With a page break
    added after that, a table that fills its page pushes the empty paragraph onto the next one and
    the break then opens another: a blank page. So the empty tail goes, and the next document's
    title carries the break itself (pageBreakBefore), which can never leave a page empty.
    """
    merged = docx.Document(paths[0])
    body = merged.element.body
    for path in paths[1:]:
        # the section properties, which must stay last, and the empty paragraphs before them
        while len(body) > 1 and _is_empty_paragraph(body[-2]):
            body.remove(body[-2])
        elements = [element for element in docx.Document(path).element.body if element.tag != qn("w:sectPr")]
        for index, element in enumerate(elements):
            copied = copy.deepcopy(element)
            if index == 0 and copied.tag == qn("w:p"):
                _starts_new_page(copied)
            body[-1].addprevious(copied)
    buffer = io.BytesIO()
    merged.save(buffer)
    return buffer.getvalue()


def write_report(writers: list[Callable[[], Any]], name: str) -> str:
    """Run mento's document writers in a scratch folder and hand the file back as base64."""
    previous = os.getcwd()
    with tempfile.TemporaryDirectory() as folder:
        os.chdir(folder)
        try:
            paths: list[str] = []
            # mento writes one file per check, named in the report language.
            for write in writers:
                with contextlib.redirect_stdout(io.StringIO()):
                    write()
                paths += [
                    os.path.join(folder, found)
                    for found in sorted(os.listdir(folder))
                    if found not in map(os.path.basename, paths)
                ]
            content = merge_documents(paths)
        finally:
            os.chdir(previous)
    return base64.b64encode(content).decode("ascii")


def safe_label(label: str) -> str:
    """mento puts the label in the names of the files it writes, so it has to be a valid one."""
    return "".join("_" if char in r'\/:*?"<>|' else char for char in label)


# ------------------------------------------------------------------------ the list of saved sections
# What every calculator's list of saved sections hands over: one Excel in the input format of mento's
# summary of that element (a sheet of rows under a row of units).


def shared_materials(items: list[dict[str, Any]]) -> Units:
    """The system of a list of saved sections, once they all share it and their materials: a summary
    takes one concrete and one steel. ``InputError`` on ``beams`` otherwise, or when the list is empty."""
    if not items:
        raise InputError("list", "empty")
    first = items[0]
    for other in items[1:]:
        if (other.get("units") or "si") != (first.get("units") or "si"):
            raise InputError("list", "units")
        if any(other.get(key) != first.get(key) for key in ("code", "fc", "fy")):
            raise InputError("list", "materials")
    materials(first)  # the code, fc and fy are ones mento knows
    return units_of(first)


def summary_file(sheet: str, name: str, columns: list[str], units_row: list[str], rows: list[list[Any]]) -> str:
    """The Excel of a summary, as ``[{"name", "base64"}]``: the headings, the units row, the rows."""
    import pandas as pd

    frame = pd.DataFrame([units_row, *rows], columns=columns, dtype=object)
    out = io.BytesIO()
    frame.to_excel(out, sheet_name=sheet, index=False)
    return json.dumps([{"name": name, "base64": base64.b64encode(out.getvalue()).decode()}])


def summary_forces(data: dict[str, Any], units: Units) -> list[tuple[str, float, float, float]]:
    """The combinations of a section, as the sheet writes them: label, Nx, Vz and My in the system's units."""
    return [
        (
            force.label,
            round(float(force.N_x.to(units.force).magnitude), 6),
            round(float(force.V_z.to(units.force).magnitude), 6),
            round(float(force.M_y.to(units.moment).magnitude), 6),
        )
        for force in build_forces(data)
    ]
