// The shear wall: in-plane shear with a horizontal and a vertical mesh, which is what mento
// checks today. Everything it shares with the other calculators is in shared/calculator.js.
import { CONCRETE_CLASS, dimBelow, dimLeft, fmt, num, pyUnits, start } from "../shared/calculator.js";

const EXAMPLE = {
  code: "CIRSOC 201-25", fc: 25, fy: 420, thickness: 20, length: 300, wall_height: 300, cover: 25,
  label: "T-101", mode: "design",
  forces: [
    { label: "1.2D+1.0E", M_y: 0, V_z: 700, N_x: 400 },
    { label: "0.9D+1.0E", M_y: 0, V_z: 620, N_x: 0 },
  ],
  rebar: { horizontal: { d: 8, s: 20 }, vertical: { d: 10, s: 30 } },
  choice: {},
};
// The same wall for the US (py/wall.py EXAMPLE_US): 8 in thick, 10 ft long and high, ACI 318-19.
const EXAMPLE_US = {
  code: "ACI 318-19", fc: 4000, fy: 60, thickness: 8, length: 10, wall_height: 10, cover: 1,
  label: "W-101", mode: "design",
  forces: [
    { label: "1.2D+1.0E", M_y: 0, V_z: 190, N_x: 90 },
    { label: "0.9D+1.0E", M_y: 0, V_z: 165, N_x: 0 },
  ],
  rebar: { horizontal: { d: 3, s: 11 }, vertical: { d: 3, s: 11 } },
  choice: {},
};

// A horizontal cut of the wall: the thickness across, the length along, the vertical bars as dots.
// In US units the cut arrives in inches, its length too; the label gives the length in feet.
function drawing(result, t, phone, U) {
  const { length, thickness, bars } = result.section;
  const box = phone ? { w: 343, h: 210 } : { w: 556, h: 300 };
  // Dimension lines need their room on the left and below, the frame label its band on top;
  // the "l × t" note stays as the figure's description.
  const note = `${length} × ${thickness}`;
  const room = { left: 60, right: 24 };
  const scale = Math.min((box.w - room.left - room.right) / length, (box.h - 120) / thickness, 4 * U.scale);
  const [w, h] = [length * scale, thickness * scale];
  const x0 = room.left + (box.w - room.left - room.right - w) / 2;
  // centred, but never up into the band of the frame's label and Copiá button
  const y0 = Math.max((box.h - h - 50) / 2, phone ? 60 : 44);
  const parts = [`<rect class="dw-concrete" x="${x0}" y="${y0}" width="${w}" height="${h}"/>`];
  for (const bar of bars) {
    parts.push(`<circle class="dw-bar" cx="${(x0 + bar.x * scale).toFixed(2)}"`
      + ` cy="${(y0 + bar.y * scale).toFixed(2)}" r="${Math.max(1.8, (bar.d / 2) * scale).toFixed(2)}"/>`);
  }
  if (result.rebar.horizontal) {
    parts.push(`<text class="dw-label" x="${x0}" y="${y0 - 10}">${result.rebar.horizontal}</text>`);
  }
  if (result.rebar.vertical) {
    parts.push(`<text class="dw-label" x="${x0}" y="${y0 + h + 22}">${result.rebar.vertical}</text>`);
  }
  // dimension lines: the thickness on the left, the width under the bottom label (its extension
  // lines start below the label, so they never cross it)
  parts.push(dimLeft(y0, y0 + h, x0, x0 - 14, `${fmt(thickness)} ${U.len}`),
    dimBelow(x0, x0 + w, y0 + h + 26, y0 + h + 38, `${fmt(U.toSpan(length))} ${U.span}`));
  const description = t.fig_section.replace("{section}", note)
    .replace("{bars}", [result.rebar.horizontal, result.rebar.vertical].filter(Boolean).join(", "));
  return `<svg viewBox="0 0 ${box.w} ${box.h}" width="${box.w}" height="${box.h}" style="max-width:100%;height:auto"`
    + ` role="img" aria-label="${description}">${parts.join("")}</svg>`;
}

function python(state, result) {
  const concrete = CONCRETE_CLASS[state.code];
  const P = pyUnits(state.units);
  const lines = [
    `from mento import ${concrete}, SteelBar, ShearWall, Forces`,
    P.imports,
    "",
    `concrete = ${concrete}(name="${state.code}", f_c=${P.fc(state.fc)})`,
    `steel = SteelBar(name="fy ${state.fy}", f_y=${P.fy(state.fy)})`,
    `wall = ShearWall(label="${state.label}", concrete=concrete, steel_bar=steel, c_c=${P.cov(num(state.cover))},`,
    `                 thickness=${P.len(num(state.thickness))}, length=${P.span(num(state.length))},`
    + ` height=${P.span(num(state.wall_height))})`,
    "forces = [",
    ...state.forces.filter((force) => num(force.M_y) || num(force.V_z) || num(force.N_x)).map((force) =>
      `    Forces(label="${force.label}", M_y=${P.moment(num(force.M_y) || 0)}, V_z=${P.force(num(force.V_z) || 0)},`
      + ` N_x=${P.force(num(force.N_x) || 0)}),`),
    "]",
    "",
  ];
  const layouts = state.mode === "check"
    ? { horizontal: mesh(state.rebar.horizontal), vertical: mesh(state.rebar.vertical) }
    : result?.layouts;
  if (state.mode === "design") {
    lines.push("# Let mento design the mesh", "wall.design_shear(forces)");
  } else if (layouts) {
    lines.push("# Set the mesh");
    if (layouts.horizontal) lines.push(`wall.set_horizontal_rebar(d_b=${P.bar(layouts.horizontal.d)}, s=${P.len(layouts.horizontal.s)})`);
    if (layouts.vertical) lines.push(`wall.set_vertical_rebar(d_b=${P.bar(layouts.vertical.d)}, s=${P.len(layouts.vertical.s)})`);
  }
  return lines.concat([
    "",
    "# Perform all checks",
    "wall.check(forces)",
    "# Does it comply? The DCR is not all: mento lists the code limits the section misses",
    "print(wall.shear_design.DCR <= 1)",
    "for warning in wall.warnings:",
    "    print(warning.code, warning.message)",
    "# Print results in Markdown format",
    "wall.results",
    "# Print shear results in more detailed format in a DataFrame",
    "wall.check_shear(forces)",
    "# View detailed shear results",
    "wall.shear_results_detailed()",
  ]).join("\n");
}

const mesh = (group) => (Number(group.d) && Number(group.s) ? { d: Number(group.d), s: Number(group.s) } : null);

const barsFromLayouts = (layouts) => ({
  horizontal: { d: layouts.horizontal?.d || 0, s: layouts.horizontal?.s || 0 },
  vertical: { d: layouts.vertical?.d || 0, s: layouts.vertical?.s || 0 },
});

start({
  module: "wall",
  collection: { key: "walls", fallback: "W1", size: (p, U) => `${fmt(p.thickness)} ${U.len} × ${fmt(p.length)} ${U.span}` },
  example: EXAMPLE,
  exampleUS: EXAMPLE_US,
  numbers: [
    { id: "thickness", unit: "len" },
    { id: "length", unit: "span" },
    { id: "wall_height", unit: "span" },
    { id: "cover", unit: "cov", rule: (state, number, U) => (number(state.thickness) <= 2 * U.cover(number(state.cover)) ? "err_cover" : "") },
  ],
  forces: ["M_y", "V_z", "N_x"],
  groups: [{ key: "horizontal", label: "horizontal" }, { key: "vertical", label: "vertical" }],
  bars: {
    horizontal_d: ["horizontal", "d"], horizontal_s: ["horizontal", "s"],
    vertical_d: ["vertical", "d"], vertical_s: ["vertical", "s"],
  },
  tables: ["shear"],
  drawing,
  // the section as typed, before the first result: its bars come in with it
  // the length is typed in cm or ft; the cut is drawn in the section's unit, cm or in
  preview: (state, U) => ({
    section: { length: num(state.length) / U.toSpan(1), thickness: num(state.thickness), cover: U.cover(num(state.cover)), bars: [] },
    rebar: {},
  }),
  python,
  barsFromLayouts,
});
