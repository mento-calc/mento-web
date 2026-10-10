"""Glue between the shear wall page and mento: JSON in, JSON out.

In-plane shear only, which is what mento checks today, under ACI 318-19, CIRSOC 201-25 and
EN 1992-1-1: a horizontal mesh that carries the shear and a vertical mesh that covers the minimum::

    python -c "import wall, json; print(wall.run(json.dumps(wall.EXAMPLE)))"
"""

from __future__ import annotations

import json
import math
import warnings
from typing import Any

import common
import mento
from mento import Forces, ShearWall, ureg

# The worked example every visitor opens: a 20 cm wall, 3 m long, one storey high.
EXAMPLE: dict[str, Any] = {
    "code": "CIRSOC 201-25",
    "lang": "es",
    "mode": "design",
    "label": "T-101",
    "fc": 25,
    "fy": 420,
    "thickness": 20,
    "length": 300,
    "wall_height": 300,
    "cover": 25,
    "forces": [
        {"label": "1.2D+1.0E", "M_y": 0, "V_z": 700, "N_x": 400},
        {"label": "0.9D+1.0E", "M_y": 0, "V_z": 620, "N_x": 0},
    ],
}
# The same wall in US customary units: 8 in thick, 10 ft long and high, ACI 318-19. Length and
# height go in feet, as a US drawing gives them; thickness and cover in inches.
EXAMPLE_US: dict[str, Any] = {
    "code": "ACI 318-19",
    "units": "us",
    "lang": "en",
    "mode": "design",
    "label": "W-101",
    "fc": 4000,
    "fy": 60,
    "thickness": 8,
    "length": 10,
    "wall_height": 10,
    "cover": 1,
    "forces": [
        {"label": "1.2D+1.0E", "M_y": 0, "V_z": 190, "N_x": 90},
        {"label": "0.9D+1.0E", "M_y": 0, "V_z": 165, "N_x": 0},
    ],
}
GROUPS = ("horizontal", "vertical")


def _build(data: dict[str, Any]) -> ShearWall:
    concrete, steel = common.materials(data)
    units = common.units_of(data)
    return ShearWall(
        label=common.label_of(data, "W1"),
        concrete=concrete,
        steel_bar=steel,
        c_c=common.number(data, "cover") * ureg(units.cover),
        thickness=common.number(data, "thickness") * ureg(units.length),
        length=common.number(data, "length") * ureg(units.span),
        height=common.number(data, "wall_height") * ureg(units.span),
    )


def _mesh(wall: ShearWall, group: str, units: common.Units) -> dict[str, Any]:
    """The mesh mento designed, read off the wall (``wall.mesh``; a wall has no ``reinforcement``)."""
    direction = getattr(wall.mesh, group)
    if not direction.has_bars:
        return {}
    return {"d": units.bar_of(direction.d_b), "s": round(float(direction.s.to(units.length).magnitude), 3)}


def _rebar_layouts(rebar: dict[str, Any]) -> dict[str, Any]:
    layouts = {}
    for group in GROUPS:
        values = rebar.get(group) or {}
        diameter, spacing = float(values.get("d") or 0), float(values.get("s") or 0)
        layouts[group] = {"d": diameter, "s": spacing} if diameter and spacing else {}
    if not layouts["horizontal"]:
        raise common.InputError("rebar", "horizontal")
    return layouts


def _apply(wall: ShearWall, layouts: dict[str, Any], units: common.Units) -> None:
    horizontal, vertical = layouts.get("horizontal") or {}, layouts.get("vertical") or {}
    if horizontal:
        wall.set_horizontal_rebar(d_b=units.bar(horizontal["d"]), s=horizontal["s"] * ureg(units.length))
    if vertical:
        wall.set_vertical_rebar(d_b=units.bar(vertical["d"]), s=vertical["s"] * ureg(units.length))


def _checked(data: dict[str, Any], forces: list[Forces], layouts: dict[str, Any]) -> tuple[ShearWall, Any]:
    """A fresh wall with those meshes in it, checked; the check table comes back for the page."""
    wall = _build(data)
    _apply(wall, layouts, common.units_of(data))
    return wall, wall.check_shear(forces)


def _dcr(wall: ShearWall) -> float:
    return round(float(wall.shear_design.DCR), 3)


def _options(wall: ShearWall, layouts: dict[str, Any], units: common.Units, limit: int = 3) -> dict[str, Any]:
    """Alternative meshes: the same steel ratio with another bar diameter.

    mento designs one mesh per direction and offers no alternatives for a wall, so they are built
    from the ratio its own design had to reach, over every combination — ρt,req (never below
    ρt,min) for the horizontal mesh, ρl,min for the vertical one — and every one of them is then
    checked by mento.
    """
    design = wall.shear_design
    required = {"horizontal": max(design.rho_t_req, design.rho_t_min), "vertical": design.rho_l_min}
    # The widest each mesh may be is the code's, for this wall: 3h, 45 cm and a fraction of its length
    # under ACI 318-19 §11.7, 40 cm (and 3h for the vertical bars) under EN 1992-1-1 §9.6.
    widest = {"horizontal": design.s_h_max, "vertical": design.s_v_max}
    thickness = float(wall.thickness.to(units.length).magnitude)
    options: dict[str, Any] = {}
    for group in GROUPS:
        # ρ = (two curtains × A_b) / (t × s), so the steel the ratio asks for is ρ·t·100 cm²/m
        # (ρ·t·12 in²/ft) over both curtains, and half of that is what one curtain has to provide.
        per_curtain = required[group] * thickness * units.run / 2
        max_spacing = float(widest[group].to(units.length).magnitude)
        candidates = common.spacing_options(per_curtain, layouts[group], limit, max_spacing, units)
        options[group] = [
            {
                "bars": common.mesh_bars(layout, units),
                "area": common.mesh_area(layout, units, curtains=2),
                "signature": common.signature(layout),
                "layout": layout,
            }
            for layout in candidates
            if layout
        ]
    return options


def _ledger(wall: ShearWall, code: str, units: common.Units) -> list[dict[str, Any]]:
    """One row: in-plane shear, which is the only resistance mento checks on a wall today."""
    symbols = common.CAPACITY.get(code, common.DEFAULT_CAPACITY)
    # the combination shear_design takes its capacity from: the largest DCR, the smallest ØVn
    governing = min(wall.shear_checks, key=lambda check: (-check.DCR, check.V_capacity.magnitude))
    return [
        {
            "key": "shear_in_plane",
            "dcr": round(float(governing.DCR), 3),
            "symbol": symbols["V"],
            "demand_symbol": "Vu",
            "capacity": common.magnitude(governing.V_capacity, units.force),
            "demand": round(abs(float(governing.V_u.to(units.force).magnitude)), 1),
            "unit": units.labels["force"],
            "combo": governing.label,
        }
    ]


def _section(wall: ShearWall, layouts: dict[str, Any], units: common.Units) -> dict[str, Any]:
    """A horizontal cut of the wall in cm (or in, its length too): thickness across, length along,
    one dot per bar."""
    thickness = float(wall.thickness.to(units.length).magnitude)
    length = float(wall.length.to(units.length).magnitude)
    cover = float(wall.c_c.to(units.length).magnitude)
    bars: list[dict[str, float]] = []
    vertical = layouts.get("vertical") or {}
    if vertical:
        diameter = units.size(vertical["d"])
        spacing = vertical["s"]
        count = max(2, math.ceil(length / spacing))
        start = (length - (count - 1) * spacing) / 2
        for index in range(count):
            x = start + index * spacing
            if -1e-6 <= x <= length + 1e-6:
                for y in (cover + diameter / 2, thickness - cover - diameter / 2):
                    bars.append({"x": round(x, 3), "y": round(y, 3), "d": round(diameter, 3)})
    return {"length": length, "thickness": thickness, "cover": cover, "unit": units.labels["length"], "bars": bars}


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
        designed = _build(data)
        designed.design(forces)
        options = _options(designed, {group: _mesh(designed, group, units) for group in GROUPS}, units)
        selected, changed = common.select(options, data.get("choice") or {})
        layouts = {group: common.selected_layout(options, group, selected) for group in options}

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        wall, frame = _checked(data, forces, layouts)
        detailed = common.detailed(wall.shear_results_detailed)

    if not check_mode:

        def evaluate(candidate: dict[str, Any], _group: str) -> tuple[float, None]:
            return _dcr(_checked(data, forces, candidate)[0]), None

        current = {group: (_dcr(wall), None) for group in options}
        common.fill_option_dcrs(options, selected, current, evaluate)
        # The vertical mesh is minimum steel: it has no DCR of its own, so it shows "—" (6).
        for option in options["vertical"]:
            option["dcr"] = None

    return {
        "ok": True,
        "version": mento.__version__,
        "code": data["code"],
        "units": units.name,
        "rebar": {group: common.mesh_bars(layout, units) for group, layout in layouts.items()},
        "layouts": layouts,
        "options": options,
        "selected": selected,
        "changed": changed,
        "complies": wall.shear_design.DCR <= 1,
        "ledger": _ledger(wall, data["code"], units),
        "notices": common.warning_notices(wall, list(captured)),
        "tables": {"shear": common.table(frame)},
        "section": _section(wall, layouts, units),
        **detailed,
    }


def run(payload: str) -> str:
    """Design or check a wall. Never raises: errors come back as ``{"ok": false}``."""
    try:
        result = _solve(json.loads(payload))
    except common.InputError as error:
        result = {"ok": False, "kind": "input", "field": error.field, "message": str(error)}
    except Exception as error:  # noqa: BLE001 - everything must reach the page as JSON
        result = {"ok": False, "kind": "mento", "message": f"{type(error).__name__}: {error}"}
    return json.dumps(result)


def report(payload: str) -> str:
    """Build the Word report of the shear check as ``[{"name", "base64"}]``."""
    data = json.loads(payload)
    lang = data.get("lang", "en")
    mento.set_language(lang if lang in mento.available_languages() else "en")
    data["label"] = common.safe_label(common.label_of(data, "W1"))
    forces = common.build_forces(data)
    units = common.units_of(data)
    if data.get("mode") == "check":
        layouts = _rebar_layouts(data.get("rebar") or {})
    else:
        designed = _build(data)
        designed.design(forces)
        options = _options(designed, {group: _mesh(designed, group, units) for group in GROUPS}, units)
        selected, _ = common.select(options, data.get("choice") or {})
        layouts = {group: common.selected_layout(options, group, selected) for group in options}
    wall, _ = _checked(data, forces, layouts)
    content = common.write_report([wall.shear_results_detailed_doc], wall.label)
    return json.dumps([{"name": f"{wall.label} - {data['code']}.docx", "base64": content}])
