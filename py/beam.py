"""Glue between the web page and mento: JSON in, JSON out.

Runs inside Pyodide in the browser, but has no Pyodide-specific code, so it can
be exercised from a normal interpreter too::

    python -c "import beam, json; print(beam.run(json.dumps(beam.EXAMPLE)))"
"""

from __future__ import annotations

import json
import math
import warnings
from typing import Any

import common
import mento
from mento import BeamSummary, Forces, Node, RectangularBeam, ureg
from mento.cage_detailing import CageDetailingError, build_cage_detailing
from mento.design_results import STIRRUPS, transverse_notation
from mento.plots.sections import steel_ratio

# The worked example every visitor opens: it must pass with every DCR between 0.7 and 0.9, and
# the home's hero shows its result (py/test_site.py checks that the two agree).
EXAMPLE: dict[str, Any] = {
    "code": "CIRSOC 201-25",
    "lang": "es",
    "mode": "design",
    "label": "V-101",
    "fc": 25,
    "fy": 420,
    "width": 20,
    "height": 60,
    "cover": 25,
    "forces": [
        {"label": "1.4D", "M_y": 55, "V_z": 45, "N_x": 0},
        {"label": "1.2D+1.6L", "M_y": 92, "V_z": 70, "N_x": 0},
        {"label": "0.9D+1.0E", "M_y": -45, "V_z": 65, "N_x": 30},
    ],
}
# The same beam for the US: inches, psi, Grade 60 and kip-ft, under ACI 318-19. Held to the same
# rule: it passes, with the bottom flexure governing between 0.7 and 0.9.
EXAMPLE_US: dict[str, Any] = {
    "code": "ACI 318-19",
    "units": "us",
    "lang": "en",
    "mode": "design",
    "label": "B-101",
    "fc": 4000,
    "fy": 60,
    "width": 12,
    "height": 24,
    "cover": 1.5,
    "forces": [
        {"label": "1.4D", "M_y": 45, "V_z": 20, "N_x": 0},
        {"label": "1.2D+1.6L", "M_y": 72, "V_z": 30, "N_x": 0},
        {"label": "0.9D+1.0E", "M_y": -58, "V_z": 26, "N_x": 7},
    ],
}


def _covers(provided: Any, required: Any) -> bool:
    """Whether the stirrups placed reach what the shear requires (never below its minimum)."""
    if required is None:
        return True
    return bool(provided.to(required.units).magnitude >= required.magnitude * 0.999)


def _build_beam(data: dict[str, Any]) -> RectangularBeam:
    concrete, steel = common.materials(data)
    units = common.units_of(data)
    return RectangularBeam(
        label=common.label_of(data, "B1"),
        concrete=concrete,
        steel_bar=steel,
        width=common.number(data, "width") * ureg(units.length),
        height=common.number(data, "height") * ureg(units.length),
        c_c=common.number(data, "cover") * ureg(units.cover),
    )


def _row(layout: dict[str, Any], layer: int) -> list[tuple[int, float]]:
    """The bar groups of one row of a face (1 nearest the face, 2 behind it): ``(n, d)`` pairs, ``d``
    as the layout names the bar (its diameter in mm, or its ASTM size).

    A layout names the row of every group, as mento's setters do: n1 and n2 are the corner and
    the inner bars of the first row, n3 and n4 those of the second.
    """
    return [
        (int(layout[f"n{index}"]), float(layout[f"d{index}"]))
        for index in (2 * layer - 1, 2 * layer)
        if layout.get(f"n{index}") and layout.get(f"d{index}")
    ]


def _rows(layout: dict[str, Any]) -> list[list[tuple[int, float]]]:
    """The rows of a face that carry bars, nearest the face first."""
    return [row for row in (_row(layout, 1), _row(layout, 2)) if row]


def _geometry(beam: RectangularBeam) -> tuple[Any, bool]:
    """The cage as mento details it (``beam.detailing_geometry``), and whether its stirrups can be
    drawn: the bars seated in the stirrup bends, a mounting bar wherever a corner or a leg has no
    bar to hold, the skin bars of a deep beam.

    A cage mento cannot detail is drawn as ``beam.plot()`` draws it: without the skin when the skin
    is what does not fit, otherwise as the checks assume it (``beam.section_geometry``), and without
    stirrups when they are too narrow for their own bends. ``beam.warnings`` says what failed."""
    try:
        return beam.detailing_geometry, True
    except CageDetailingError as error:
        if error.reason == "skin":
            try:
                return build_cage_detailing(beam, include_skin=False), True
            except CageDetailingError as base_error:
                error = base_error
        return beam.section_geometry, error.reason != "bend"


def _section(beam: RectangularBeam, layouts: dict[str, Any], units: common.Units) -> dict[str, Any]:
    """The section in cm (or in), origin at the bottom left corner, for the page to draw: mento's own
    geometry, piece by piece, with a label for every row of bars at that row's height.

    ``closed`` are the closed stirrups and ``ties`` the legs between them, by their centrelines.
    A bar is "main" (the steel the checks count), "mounting" or "skin"; neither of the last two
    adds to a resistance. ``steel`` is the steel of the whole cage per volume of concrete."""
    geometry, with_stirrups = _geometry(beam)

    def length(value: Any) -> float:
        return round(float(value.to(units.length).magnitude), 3)

    def name(bars: Any) -> str:
        """``2Ø8`` for bars of one diameter that no layout names: mounting and skin."""
        return f"{len(bars)}{units.bar_name(units.bar_of(bars[0].d_b))}"

    def height(bars: Any) -> float:
        """The middle of the band a row's bars occupy, where ``beam.plot()`` writes its label."""
        low = min(bar.y - bar.d_b / 2 for bar in bars)
        high = max(bar.y + bar.d_b / 2 for bar in bars)
        return length((low + high) / 2)

    kinds = (("main", geometry.bars), ("mounting", geometry.mounting_bars), ("skin", geometry.skin_bars))
    bars = [
        {"x": length(bar.x), "y": length(bar.y), "d": length(bar.d_b), "kind": kind}
        for kind, group in kinds
        for bar in group
    ]
    labels: list[dict[str, Any]] = []
    for face, group in (("top", "top"), ("bottom", "bot")):
        for layer in (1, 2):
            row, placed = _row(layouts.get(group) or {}, layer), geometry.bars_on(face, layer)
            if row and placed:
                labels.append({"y": height(placed), "bars": _group_bars(row, units), "kind": group})
        mounting = [bar for bar in geometry.mounting_bars if bar.face == face]
        if mounting:
            labels.append({"y": height(mounting), "bars": name(mounting), "kind": "mounting", "face": group})
    skin = [bar for bar in geometry.skin_bars if bar.face == "left"]
    if skin:  # the same bars on each side face
        labels.append({"y": height(skin), "bars": name(skin), "kind": "skin"})

    width = length(geometry.width)
    closed = geometry.stirrups if with_stirrups else ()
    ties = geometry.crossties if with_stirrups else ()
    ratio = steel_ratio(beam, geometry)
    return {
        "width": width,
        "height": length(geometry.height),
        "cover": length(geometry.c_c),
        "unit": units.labels["length"],
        # the bar the cage is bent from and the inside diameter of its bends
        "stirrup": {"d": length(geometry.stirrup_d_b), "bend": length(geometry.stirrup_bend_inner_diameter)},
        "closed": [
            {"x0": length(s.x_left), "x1": length(s.x_right), "y0": length(s.y_bottom), "y1": length(s.y_top)}
            for s in closed
        ],
        # a tie mento detailed says which side its hooks open to; the others open towards the middle
        "ties": [
            {
                "x": length(tie.x),
                "y0": length(tie.y_bottom),
                "y1": length(tie.y_top),
                "side": int(tie.side) if tie.extension is not None else (1 if length(tie.x) <= width / 2 else -1),
            }
            for tie in ties
        ],
        "bars": bars,
        "labels": labels,
        "steel": f"{ratio.magnitude:.0f} {'lb/yd³' if units.us else 'kg/m³'}",
    }


def _extras(section: dict[str, Any], beam: RectangularBeam) -> dict[str, Any]:
    """What the cage carries beyond the bars the checks count, as the drawing shows it: the skin of
    each side face, required by the code or proposed by mento's own criterion (every beam 60 cm or
    24 in deep, or more), and the mounting bars of each face."""
    labels = section["labels"]
    skin = next((label["bars"] for label in labels if label["kind"] == "skin"), None)
    return {
        "skin": {"bars": skin, "status": beam.skin_reinforcement.status} if skin else None,
        "mounting": [{"face": label["face"], "bars": label["bars"]} for label in labels if label["kind"] == "mounting"],
    }


def _group_bars(row: list[tuple[int, float]], units: common.Units) -> str:
    """``2Ø16+1Ø12`` (``2#5+1#4``) for one row: bars of one size counted together."""
    counts: dict[float, int] = {}
    for n, d in row:
        counts[d] = counts.get(d, 0) + n
    return "+".join(f"{n}{units.bar_name(d)}" for d, n in counts.items())


def _cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    if isinstance(value, bool):
        return "✓" if value else "✕"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _table(frame: Any) -> dict[str, Any]:
    """mento's DataFrame as it prints it: same columns, same order, its units row in the header."""
    columns = [str(name) for name in frame.columns]
    rows = [[_cell(value) for value in row] for row in frame.itertuples(index=False)]
    # mento puts the units in the first row, under an empty label
    units = rows.pop(0) if rows and not rows[0][0].strip() else [""] * len(columns)
    return {
        "columns": columns,
        "units": units,
        "rows": rows,
        "dcr": columns.index("DCR") if "DCR" in columns else None,
    }


def _bars(layers: Any, units: common.Units) -> str:
    """``3Ø16+2Ø12``: bars of one size are counted together, the way they are ordered on site."""
    counts: dict[float, int] = {}
    for layer in layers:
        d = units.bar_of(layer.d_b)
        counts[d] = counts.get(d, 0) + layer.n
    return "+".join(f"{n}{units.bar_name(d)}" for d, n in counts.items())


def _face(face: Any, units: common.Units) -> dict[str, Any]:
    """One face as mento judges it. ``A_s_min_eff`` is the minimum the face has to meet: under
    ACI 318-19 and CIRSOC 201-25 the 4/3 of §9.6.1.3 can leave it below ``A_s_min``. ``complies``
    is the verdict: a DCR of at most 1 and, under those codes, a tension-controlled section."""
    return {
        "bars": _bars(face.layers, units),
        "A_s": units.show(face.A_s, "area"),
        "A_s_req": units.show(face.A_s_req, "area"),
        "A_s_min": units.show(face.A_s_min, "area"),
        "A_s_min_eff": units.show(face.A_s_min_eff, "area"),
        "A_s_max": units.show(face.A_s_max, "area"),
        "M_capacity": units.show(face.M_capacity, "moment", 1),
        "DCR": round(float(face.DCR), 3),
        "complies": bool(face.complies),
    }


# ------------------------------------------------------------------ rebar layouts
# A layout is what the page, the URL and mento's setters all speak: {"n1": 2, "d1": 16, ...}
# for a face and {"n": 2, "d": 6, "s": 13} for the stirrups, where "n" counts their legs as mento
# does (two per closed stirrup; an odd number from 3 adds a leg to the perimeter stirrup).
# Diameters in mm and spacing in cm, or ASTM sizes and spacing in inches ({"n1": 2, "d1": 6, ...} is 2#6).


# A design never leaves its bars closer than the minimum, nor its top bars without room for the vibrator.
_FIT_CODES = ("clear_spacing_below_min", "clear_spacing_below_vibrator", "bars_do_not_fit")


def _fits_one_row(data: dict[str, Any], stirrups: dict[str, Any], face: str, layers: Any) -> bool:
    """Whether two bar groups fit side by side in the row nearest the face, as mento judges the
    spacing: on a fresh beam with the stirrups the design finished with, read off its warnings
    (the bar spacing needs no check)."""
    probe = _build_beam(data)
    units = common.units_of(data)
    if stirrups:
        probe.set_transverse_rebar(
            n_legs=int(stirrups["n"]), d_b=units.bar(stirrups["d"]), s_l=stirrups["s"] * ureg(units.length)
        )
    first, second = layers
    setter = probe.set_longitudinal_rebar_bot if face == "bot" else probe.set_longitudinal_rebar_top
    setter(n1=int(first.n), d_b1=first.d_b, n2=int(second.n), d_b2=second.d_b)
    name = "bottom" if face == "bot" else "top"
    return not any(warning.code in _FIT_CODES and warning.face == name for warning in probe.warnings)


def _layers_layout(data: dict[str, Any], stirrups: dict[str, Any], face: str, layers: Any) -> dict[str, Any]:
    """A face's layout, with the row of every group, from the layers of a mento ``RebarOption``.

    Those layers are the groups n_1..n_4 of the search that carry bars, in order, so an empty
    group leaves no trace: two layers are n_1 + n_2 in one row, or n_1 and n_3 in two, and three
    are n_1 + n_2 | n_3 or n_1 | n_3 + n_4. The search ranks a second row below a first that
    holds the same bars, and mento keeps one option per set of layers, the better ranked; so the
    groups go in the first row when they fit there and in the second when they do not.
    """
    layers = [layer for layer in layers if layer.n][:4]
    slots = (1, 2, 3, 4)
    if len(layers) in (2, 3) and not _fits_one_row(data, stirrups, face, layers[:2]):
        slots = (1, 3, 4)
    units = common.units_of(data)
    layout: dict[str, Any] = {}
    for slot, layer in zip(slots, layers, strict=False):
        layout[f"n{slot}"] = int(layer.n)
        layout[f"d{slot}"] = units.bar_of(layer.d_b)
    return layout


def _stirrup_layout(stirrups: Any, units: common.Units) -> dict[str, Any]:
    """The layout of the stirrups a section carries, or of one mento's design offers."""
    if not stirrups.n_legs:
        return {}
    spacing = round(float(stirrups.s_l.to(units.length).magnitude), 3)
    return {"n": int(stirrups.n_legs), "d": units.bar_of(stirrups.d_b), "s": spacing}


def _rebar_layouts(rebar: dict[str, Any]) -> dict[str, Any]:
    """What the user typed in check mode, in the same shape."""

    def face(values: dict[str, Any]) -> dict[str, Any]:
        """n1 + n2 in the first row, n3 + n4 in the second, as the page and mento number them."""
        layout: dict[str, Any] = {}
        for index in range(1, 5):
            n = int(values.get(f"n{index}") or 0)
            if n:
                layout[f"n{index}"] = n
                layout[f"d{index}"] = float(values.get(f"d{index}") or 0)
        return layout

    bottom = face(rebar.get("bot") or {})
    if not bottom:
        raise common.InputError("rebar", "bottom")
    stirrups = rebar.get("stirrups") or {}
    n = int(stirrups.get("n") or 0)
    if n == 1:  # the perimeter stirrup is closed: it has two legs
        raise common.InputError("rebar", "legs")
    return {
        "bot": bottom,
        "top": face(rebar.get("top") or {}),
        "st": {"n": n, "d": float(stirrups.get("d") or 0), "s": float(stirrups.get("s") or 0)} if n else {},
    }


def _signature(layout: dict[str, Any]) -> str:
    """Short, stable name of a layout: what the URL keeps and what re-picks it after a recalc."""
    if "n" in layout:
        return f"{layout['n']:g}x{layout['d']:g}@{layout['s']:g}"
    return "+".join(f"{layout[f'n{i}']:g}x{layout[f'd{i}']:g}" for i in range(1, 5) if layout.get(f"n{i}"))


def _layout_bars(layout: dict[str, Any], units: common.Units) -> str:
    """``3Ø16+1Ø12`` and ``1eØ6/13cm``; ``3#5+1#4`` and ``1s#3@5in``.

    The stirrups are mento's own compact notation, in the language of the page, with the unit of the
    spacing after it: one closed stirrup, which is two legs, and the legs beyond it. Four legs are
    ``1e+2rØ10/17cm`` (``1s+2l#3@8in`` in English), three ``1e+1rØ8/15cm``."""
    if not layout:
        return ""
    if "n" in layout:
        spacing = layout["s"] * ureg(units.length)
        legs = transverse_notation(
            STIRRUPS, layout["n"] / 2, units.bar(layout["d"]), spacing, spacing, compact=True, imperial=units.us
        )
        return f"{legs}{units.labels['length']}"
    counts: dict[float, int] = {}
    for index in range(1, 5):
        n = int(layout.get(f"n{index}") or 0)
        if n:
            counts[layout[f"d{index}"]] = counts.get(layout[f"d{index}"], 0) + n
    return "+".join(f"{n}{units.bar_name(d)}" for d, n in counts.items())


def _layout_rows(layout: dict[str, Any], units: common.Units) -> list[str]:
    """The bars of a face, one string per row: ``["2Ø16+1Ø12", "2Ø12"]``."""
    if not layout or "n" in layout:
        return [_layout_bars(layout, units)] if layout else []
    return [_group_bars(row, units) for row in _rows(layout)]


def _layout_area(layout: dict[str, Any], units: common.Units) -> str:
    """The steel a layout puts in: cm² (in²) on a face, cm²/m (in²/ft) of stirrups."""
    if not layout:
        return ""
    if "n" in layout:
        legs = layout["n"] * units.bar_area(layout["d"])  # per stirrup line
        return f"{legs * units.run / layout['s']:.2f} {units.labels['per_length']}"
    total = sum(
        int(layout.get(f"n{i}") or 0) * units.bar_area(layout[f"d{i}"]) for i in range(1, 5) if layout.get(f"n{i}")
    )
    return f"{total:.2f} {units.labels['area']}"


def _apply_layouts(beam: RectangularBeam, layouts: dict[str, Any], units: common.Units) -> None:
    def kwargs(layout: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for index in range(1, 5):
            n = int(layout.get(f"n{index}") or 0)
            out[f"n{index}"] = n
            out[f"d_b{index}"] = units.bar(layout[f"d{index}"]) if n else None
        return out

    beam.set_longitudinal_rebar_bot(**kwargs(layouts.get("bot") or {}))
    # A face with no bars has none, as mento's design leaves it: not the 2Ø8 a new beam starts with.
    # The cage then holds its stirrups there with mounting bars, which add to no resistance.
    beam.set_longitudinal_rebar_top(**kwargs(layouts.get("top") or {}))
    stirrups = layouts.get("st") or {}
    if stirrups.get("n"):
        beam.set_transverse_rebar(
            n_legs=int(stirrups["n"]), d_b=units.bar(stirrups["d"]), s_l=stirrups["s"] * ureg(units.length)
        )


# ----------------------------------------------------------------- design options


def _reinforced(data: dict[str, Any], forces: list[Forces], layouts: dict[str, Any]) -> tuple[RectangularBeam, Node]:
    """A fresh beam with these bars in it, under its combinations."""
    beam = _build_beam(data)
    node = Node(section=beam, forces=forces)
    _apply_layouts(beam, layouts, common.units_of(data))
    return beam, node


def _checked(data: dict[str, Any], forces: list[Forces], layouts: dict[str, Any]) -> tuple[RectangularBeam, Node]:
    """A fresh beam with these bars in it, checked. Fresh because designing or checking leaves
    state behind: the same design run twice on one beam does not give the same stirrups."""
    beam, node = _reinforced(data, forces, layouts)
    node.check()
    return beam, node


def _group_state(beam: RectangularBeam, group: str) -> tuple[float, bool | None]:
    """The DCR of one rebar group on a checked beam, and whether that face complies."""
    if group == "st":
        return round(float(beam.shear_design.DCR), 3), None
    face = beam.flexure_design.bottom if group == "bot" else beam.flexure_design.top
    return round(float(face.DCR), 3), bool(face.complies)


def _selected_layout(options: dict[str, Any], group: str, selected: dict[str, int]) -> dict[str, Any]:
    group_options = options.get(group) or [{}]
    index = min(selected.get(group, 0), len(group_options) - 1)
    return group_options[index].get("layout", {})


def _options(data: dict[str, Any], beam: RectangularBeam) -> dict[str, list[dict[str, Any]]]:
    """The layouts mento's design offers for each group, the applied one first.

    ``options[0]`` is what the design applied; the rest are alternatives mento built on the
    finished section and kept only if it passes with them, so there may be fewer than asked for.
    """
    units = common.units_of(data)
    stirrups = [_stirrup_layout(option, units) for option in beam.shear_design.options] or [
        _stirrup_layout(beam.reinforcement.transverse, units)
    ]
    flexure = beam.flexure_design
    candidates = {
        group: [_layers_layout(data, stirrups[0], group, option.layers) for option in face.options]
        or [_layers_layout(data, stirrups[0], group, face.layers)]
        for group, face in (("bot", flexure.bottom), ("top", flexure.top))
    }
    candidates["st"] = stirrups
    options: dict[str, list[dict[str, Any]]] = {}
    for group, layouts in candidates.items():
        seen: set[str] = set()
        options[group] = []
        for layout in layouts:
            if not layout or _signature(layout) in seen:
                continue
            seen.add(_signature(layout))
            options[group].append(
                {
                    "bars": _layout_bars(layout, units),
                    "rows": _layout_rows(layout, units),
                    "area": _layout_area(layout, units),
                    "signature": _signature(layout),
                    "layout": layout,
                }
            )
    return options


def _fill_option_dcrs(
    data: dict[str, Any],
    forces: list[Forces],
    options: dict[str, Any],
    selected: dict[str, int],
    current: dict[str, tuple[float, bool | None]],
) -> None:
    """The DCR beside an option is its own group's, with the rest of the section as the user has
    it. mento's ``section_DCR`` is the worst of the whole section with the rest as the design
    applied it: the same for most options, and blind to a choice made in another group."""
    for group, group_options in options.items():
        for index, option in enumerate(group_options):
            if index == selected.get(group, 0):
                option["dcr"], option["complies"] = current[group]
                continue
            layouts = {name: _selected_layout(options, name, selected) for name in options}
            layouts[group] = option["layout"]
            beam, _ = _checked(data, forces, layouts)
            option["dcr"], option["complies"] = _group_state(beam, group)


def _select(options: dict[str, Any], choice: dict[str, Any]) -> tuple[dict[str, int], list[str]]:
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


# ------------------------------------------------------------- verdict and notices

# What only a design can say: that no layout that fits the section carries the moment, and what the
# closest one carries. mento keeps it on the beam it designed, not on one given the same bars by hand.
_SHORTFALL_CODES = ("As_below_required", "section_too_small_for_moment")


def _solve(data: dict[str, Any]) -> dict[str, Any]:
    lang = data.get("lang", "en")
    mento.set_language(lang if lang in mento.available_languages() else "en")
    forces = common.build_forces(data)
    units = common.units_of(data)
    check_mode = data.get("mode") == "check"
    options: dict[str, Any] = {}
    selected: dict[str, int] = {}
    changed: list[str] = []

    if check_mode:
        layouts = _rebar_layouts(data.get("rebar") or {})
    else:
        designed = _build_beam(data)
        Node(section=designed, forces=forces).design()
        options = _options(data, designed)
        selected, changed = _select(options, data.get("choice") or {})
        layouts = {group: _selected_layout(options, group, selected) for group in options}

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        beam, node = _checked(data, forces, layouts)
        flexure_table = node.check_flexure()
        shear_table = node.check_shear()

    if not check_mode:
        current = {group: _group_state(beam, group) for group in options}
        _fill_option_dcrs(data, forces, options, selected, current)

    flexure = beam.flexure_design
    shear = beam.shear_design
    detailed = common.detailed(node.flexure_results_detailed, node.shear_results_detailed)
    section = _section(beam, layouts, units)
    complies = bool(flexure.complies) and shear.DCR <= 1
    notices = common.warning_notices(node, list(captured))
    if not check_mode and not complies:
        shortfall = [notice for notice in common.warning_notices(designed, []) if notice["code"] in _SHORTFALL_CODES]
        notices += [notice for notice in shortfall if notice["code"] not in {known["code"] for known in notices}]
    return {
        "ok": True,
        "version": mento.__version__,
        "code": data["code"],
        "units": units.name,
        "rebar": {group: _layout_bars(layout, units) for group, layout in layouts.items()},
        "rows": {group: _layout_rows(layout, units) for group, layout in layouts.items()},
        "layouts": layouts,
        "options": options,
        "selected": selected,
        "changed": changed,
        "complies": complies,
        "ledger": [
            *common.flexure_rows(beam, forces, data["code"], units),
            common.shear_row(beam, forces, data["code"], units),
        ],
        "notices": notices,
        "flexure": {"bottom": _face(flexure.bottom, units), "top": _face(flexure.top, units)},
        "shear": {
            "stirrups": _layout_bars(layouts.get("st") or {}, units),
            "A_v": units.show(shear.A_v, "per_length"),
            "A_v_req": units.show(getattr(shear, "A_v_req", None), "per_length"),
            "V_capacity": units.show(getattr(shear, "V_capacity", None), "force", 1),
            "DCR": round(float(shear.DCR), 3),
            "enough": _covers(shear.A_v, getattr(shear, "A_v_req", None)),
        },
        "tables": {"flexure": _table(flexure_table), "shear": _table(shear_table)},
        "section": section,
        **_extras(section, beam),
        **detailed,
    }


def run(payload: str) -> str:
    """Design or check a beam. Never raises: errors come back as ``{"ok": false}``."""
    try:
        result = _solve(json.loads(payload))
    except common.InputError as error:
        result = {"ok": False, "kind": "input", "field": error.field, "message": str(error)}
    except Exception as error:  # noqa: BLE001 - everything must reach the page as JSON
        result = {"ok": False, "kind": "mento", "message": f"{type(error).__name__}: {error}"}
    return json.dumps(result)


def report(payload: str) -> str:
    """Build the Word report, flexure then shear in one file, as ``[{"name", "base64"}]``."""
    data = json.loads(payload)
    lang = data.get("lang", "en")
    mento.set_language(lang if lang in mento.available_languages() else "en")
    data["label"] = common.safe_label(common.label_of(data, "B1"))
    forces = common.build_forces(data)
    if data.get("mode") == "check":
        layouts = _rebar_layouts(data.get("rebar") or {})
    else:
        designed = _build_beam(data)
        Node(section=designed, forces=forces).design()
        options = _options(data, designed)
        selected, _ = _select(options, data.get("choice") or {})
        layouts = {group: _selected_layout(options, group, selected) for group in options}
    beam, node = _checked(data, forces, layouts)
    content = common.write_report([node.flexure_results_detailed_doc, node.shear_results_detailed_doc], beam.label)
    return json.dumps([{"name": f"{beam.label} - {data['code']}.docx", "base64": content}])


# ------------------------------------------------------------------------ the beam list
# Every beam the visitor saved, in the Excel ``BeamSummary.from_excel`` reads: a row per beam in
# Sections, with its stirrups by their legs and the bars of both faces, and a row per combination in
# Forces. mento writes it (common.summary_excel), from each beam as the page checked it.


def _saved(data: dict[str, Any]) -> Node:
    """A saved beam: what the page sent, with the layouts it got back."""
    return _reinforced(data, common.build_forces(data), data.get("layouts") or {})[1]


def summary(payload: str) -> str:
    """The saved beams as one Excel. Never raises: errors come back as ``{"ok": false}``."""
    data = json.loads(payload)
    return common.summary_excel(BeamSummary, data.get("beams") or [], _saved, "beams.xlsx", data.get("lang", "en"))
