"""Glue between the one-way slab page and mento: JSON in, JSON out.

A strip of the width the user defines, reinforced with a diameter at a spacing on each face::

    python -c "import slab, json; print(slab.run(json.dumps(slab.EXAMPLE)))"
"""

from __future__ import annotations

import json
import warnings
from typing import Any

import common
import mento
from mento import Node, OneWaySlab, ureg

# The worked example every visitor opens: a 20 cm slab spanning about 5 m, with a support moment.
EXAMPLE: dict[str, Any] = {
    "code": "CIRSOC 201-25",
    "lang": "es",
    "mode": "design",
    "label": "L-101",
    "fc": 25,
    "fy": 420,
    "width": 100,
    "height": 20,
    "cover": 20,
    "forces": [
        {"label": "1.2D+1.6L", "M_y": 32, "V_z": 38, "N_x": 0},
        {"label": "1.2D+1.6L (apoyo)", "M_y": -22, "V_z": 45, "N_x": 0},
    ],
}
# The same slab in US customary units: 8 in thick, a 12 in strip (one foot), ACI 318-19.
EXAMPLE_US: dict[str, Any] = {
    "code": "ACI 318-19",
    "units": "us",
    "lang": "en",
    "mode": "design",
    "label": "S-101",
    "fc": 4000,
    "fy": 60,
    "width": 12,
    "height": 8,
    "cover": 0.75,
    "forces": [
        {"label": "1.2D+1.6L", "M_y": 7.5, "V_z": 2.6, "N_x": 0},
        {"label": "1.2D+1.6L (support)", "M_y": -5, "V_z": 3.1, "N_x": 0},
    ],
}
GROUPS = ("bot", "top")


def _build(data: dict[str, Any]) -> OneWaySlab:
    concrete, steel = common.materials(data)
    units = common.units_of(data)
    return OneWaySlab(
        label=common.label_of(data, "L1"),
        concrete=concrete,
        steel_bar=steel,
        width=common.number(data, "width") * ureg(units.length),
        height=common.number(data, "height") * ureg(units.length),
        c_c=common.number(data, "cover") * ureg(units.cover),
    )


def _layer_layout(layers: Any, units: common.Units) -> dict[str, Any]:
    """A slab face is one bar at one spacing, which is how it is drawn and ordered."""
    for layer in layers:
        if layer.n and layer.s is not None:
            return {"d": units.bar_of(layer.d_b), "s": round(float(layer.s.to(units.length).magnitude), 3)}
    return {}


def _rebar_layouts(rebar: dict[str, Any]) -> dict[str, Any]:
    """What the user typed in check mode, in the same shape."""
    layouts = {}
    for group in GROUPS:
        values = rebar.get(group) or {}
        diameter, spacing = float(values.get("d") or 0), float(values.get("s") or 0)
        layouts[group] = {"d": diameter, "s": spacing} if diameter and spacing else {}
    if not layouts["bot"]:
        raise common.InputError("rebar", "bottom")
    return layouts


def _apply(slab: OneWaySlab, layouts: dict[str, Any], units: common.Units) -> None:
    bottom, top = layouts.get("bot") or {}, layouts.get("top") or {}
    if bottom:
        slab.set_slab_longitudinal_rebar_bot(d_b1=units.bar(bottom["d"]), s_b1=bottom["s"] * ureg(units.length))
    if top:
        slab.set_slab_longitudinal_rebar_top(d_b1=units.bar(top["d"]), s_b1=top["s"] * ureg(units.length))


def _checked(data: dict[str, Any], forces: list[Any], layouts: dict[str, Any]) -> tuple[OneWaySlab, Node]:
    """A fresh strip with that mesh in it, checked. Fresh because designing or checking leaves
    state behind on the section."""
    slab = _build(data)
    node = Node(section=slab, forces=forces)
    _apply(slab, layouts, common.units_of(data))
    node.check()
    return slab, node


def _face(slab: OneWaySlab, group: str) -> Any:
    return slab.flexure_design.bottom if group == "bot" else slab.flexure_design.top


def _state(slab: OneWaySlab, group: str) -> tuple[float, bool]:
    face = _face(slab, group)
    return round(float(face.DCR), 3), bool(face.complies)


def _per_length(area: Any, width: float, units: common.Units) -> str:
    """A strip's steel per metre (or foot) of slab: mento's area counts width / s bars, 6.67 for Ø10/15."""
    per_strip = float(area.to(units.labels["area_unit"]).magnitude)
    return f"{per_strip * units.run / width:.2f} {units.labels['per_length']}"


def _options(data: dict[str, Any], slab: OneWaySlab) -> dict[str, Any]:
    """The meshes mento's design offers for each face, the applied one first. The alternatives are
    the ones the finished strip passes with, so there may be fewer than asked for."""
    width = common.number(data, "width")
    units = common.units_of(data)
    options: dict[str, Any] = {}
    for group in GROUPS:
        face = _face(slab, group)
        options[group] = []
        for option in face.options or [face]:
            layout = _layer_layout(option.layers, units)
            if not layout or any(known["layout"] == layout for known in options[group]):
                continue
            options[group].append(
                {
                    "bars": common.mesh_bars(layout, units),
                    "area": _per_length(option.A_s, width, units),
                    "placed": option.n_bars_placed,
                    "signature": common.signature(layout),
                    "layout": layout,
                }
            )
    return options


def _section(slab: OneWaySlab, layouts: dict[str, Any], units: common.Units) -> dict[str, Any]:
    """The strip in cm (or in), with the bars of each face placed along it, for the page to draw: as
    many as mento places (``n_placed``, the whole bars that lay the spacing out across the strip)."""
    width = float(slab.width.to(units.length).magnitude)
    height = float(slab.height.to(units.length).magnitude)
    cover = float(slab.c_c.to(units.length).magnitude)
    reinforcement = slab.reinforcement
    bars: list[dict[str, float]] = []
    for group, bottom in (("bot", True), ("top", False)):
        layout = layouts.get(group) or {}
        if not layout:
            continue
        diameter = units.size(layout["d"])
        spacing = layout["s"]
        count = (reinforcement.bottom if bottom else reinforcement.top).n_bars_placed
        start = (width - (count - 1) * spacing) / 2
        y = cover + diameter / 2 if bottom else height - cover - diameter / 2
        for index in range(count):
            x = start + index * spacing
            if -1e-6 <= x <= width + 1e-6:
                bars.append({"x": round(x, 3), "y": round(y, 3), "d": round(diameter, 3)})
    return {"width": width, "height": height, "cover": cover, "unit": units.labels["length"], "bars": bars}


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
        Node(section=designed, forces=forces).design()
        options = _options(data, designed)
        selected, changed = common.select(options, data.get("choice") or {})
        layouts = {group: common.selected_layout(options, group, selected) for group in options}

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        slab, node = _checked(data, forces, layouts)
        flexure_table = node.check_flexure()
        shear_table = node.check_shear()

    if not check_mode:

        def evaluate(candidate: dict[str, Any], group: str) -> tuple[float, bool]:
            return _state(_checked(data, forces, candidate)[0], group)

        current = {group: _state(slab, group) for group in options}
        common.fill_option_dcrs(options, selected, current, evaluate)

    detailed = common.detailed(node.flexure_results_detailed, node.shear_results_detailed)
    placed, width = slab.reinforcement, common.number(data, "width")
    return {
        "ok": True,
        "version": mento.__version__,
        "code": data["code"],
        "units": units.name,
        "rebar": {group: common.mesh_bars(layout, units) for group, layout in layouts.items()},
        # a metre of Ø10/15 is 6.67 bars and 5.24 cm²; 7 of them are placed
        "placed": {group: face.n_bars_placed for group, face in (("bot", placed.bottom), ("top", placed.top))},
        "area": {
            group: _per_length(face.A_s, width, units) for group, face in (("bot", placed.bottom), ("top", placed.top))
        },
        "layouts": layouts,
        "options": options,
        "selected": selected,
        "changed": changed,
        "complies": bool(slab.flexure_design.complies) and slab.shear_design.DCR <= 1,
        "ledger": [
            *common.flexure_rows(slab, forces, data["code"], units),
            common.shear_row(slab, forces, data["code"], units),
        ],
        "notices": common.warning_notices(node, list(captured)),
        "tables": {"flexure": common.table(flexure_table), "shear": common.table(shear_table)},
        "section": _section(slab, layouts, units),
        **detailed,
    }


def run(payload: str) -> str:
    """Design or check a slab strip. Never raises: errors come back as ``{"ok": false}``."""
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
    data["label"] = common.safe_label(common.label_of(data, "L1"))
    forces = common.build_forces(data)
    if data.get("mode") == "check":
        layouts = _rebar_layouts(data.get("rebar") or {})
    else:
        designed = _build(data)
        Node(section=designed, forces=forces).design()
        options = _options(data, designed)
        selected, _ = common.select(options, data.get("choice") or {})
        layouts = {group: common.selected_layout(options, group, selected) for group in options}
    slab, node = _checked(data, forces, layouts)
    content = common.write_report([node.flexure_results_detailed_doc, node.shear_results_detailed_doc], slab.label)
    return json.dumps([{"name": f"{slab.label} - {data['code']}.docx", "base64": content}])


# ------------------------------------------------------------------------ the list of saved slabs
# Every slab the visitor saved, in the Excel OneWaySlabSummary reads (mento >= 1.5.0): one row per
# combination of each slab, with the diameter and spacing of the face its moment pulls.

SUMMARY_COLUMNS = ["Label", "Comb.", "b", "h", "cc", "Nx", "Vz", "My", "db1", "s1", "db3", "s3"]


def _summary_units(units: common.Units) -> list[str]:
    length = units.labels["length"]
    bar = "in" if units.us else "mm"
    cover = "in" if units.us else "mm"
    moment = "kip·ft" if units.us else "kNm"
    force = units.labels["force"]
    return ["", "", length, length, cover, force, force, moment, bar, length, bar, length]


def _summary_rows(data: dict[str, Any]) -> list[list[Any]]:
    units = common.units_of(data)
    layouts = data.get("layouts") or {}
    label = str(data.get("label") or "L1")
    b, h, cover = (common.number(data, key) for key in ("width", "height", "cover"))
    rows = []
    for comb, n_x, v_z, m_y in common.summary_forces(data, units):
        face = layouts.get("bot" if m_y >= 0 else "top") or {}
        bar = round(float(units.bar(face["d"]).to("inch" if units.us else "mm").magnitude), 4) if face else 0
        rows.append([label, comb, b, h, cover, n_x, v_z, m_y, bar, float(face.get("s") or 0), 0, 0])
    return rows


def summary(payload: str) -> str:
    """The saved slabs as one Excel, as ``[{"name", "base64"}]``, in the input format of
    ``OneWaySlabSummary``. Raises ``InputError`` the way ``report`` does."""
    slabs = json.loads(payload).get("slabs") or []
    units = common.shared_materials(slabs)
    rows = [row for slab_data in slabs for row in _summary_rows(slab_data)]
    return common.summary_file("Slabs", "slabs.xlsx", SUMMARY_COLUMNS, _summary_units(units), rows)
