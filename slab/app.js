// The one-way slab: a strip reinforced with a diameter at a spacing on each face.
// Everything it shares with the other calculators lives in shared/calculator.js.
import { CONCRETE_CLASS, dimBelow, dimLeft, fmt, num, pyUnits, start } from "../shared/calculator.js";

const EXAMPLE = {
  code: "CIRSOC 201-25", fc: 25, fy: 420, width: 100, height: 20, cover: 20, label: "L-101", mode: "design",
  forces: [
    { label: "1.2D+1.6L", M_y: 32, V_z: 38, N_x: 0 },
    { label: "1.2D+1.6L (apoyo)", M_y: -22, V_z: 45, N_x: 0 },
  ],
  rebar: { bot: { d: 10, s: 13 }, top: { d: 12, s: 25 } },
  choice: {},
};
// The same slab for the US (py/slab.py EXAMPLE_US): 8 in thick, a one-foot strip, ACI 318-19.
const EXAMPLE_US = {
  code: "ACI 318-19", fc: 4000, fy: 60, width: 12, height: 8, cover: 0.75, label: "S-101", mode: "design",
  forces: [
    { label: "1.2D+1.6L", M_y: 7.5, V_z: 2.6, N_x: 0 },
    { label: "1.2D+1.6L (support)", M_y: -5, V_z: 3.1, N_x: 0 },
  ],
  rebar: { bot: { d: 3, s: 4 }, top: { d: 3, s: 6 } },
  choice: {},
};

// The strip, drawn to scale: the bars of each face along it, seen in section.
function drawing(result, t, phone, U) {
  const { width, height, cover, bars } = result.section;
  const box = phone ? { w: 343, h: 210 } : { w: 556, h: 300 };
  // Dimension lines need their room on the left and below, the frame label its band on top;
  // the "w × h" note stays as the figure's description.
  const note = `${width} × ${height}`;
  const room = { left: 60, right: 24 };
  const scale = Math.min((box.w - room.left - room.right) / width, (box.h - 120) / height, 4 * U.scale);
  const [w, h] = [width * scale, height * scale];
  const x0 = room.left + (box.w - room.left - room.right - w) / 2;
  // centred, but never up into the band of the frame's label and Copiá button
  const y0 = Math.max((box.h - h - 50) / 2, phone ? 60 : 44);
  const parts = [`<rect class="dw-concrete" x="${x0}" y="${y0}" width="${w}" height="${h}"/>`];
  // The strip is cut out of a slab that goes on: the design system draws those edges dashed.
  parts.push(`<line class="dw-cut" x1="${x0}" y1="${y0 - 14}" x2="${x0}" y2="${y0 + h + 14}"/>`);
  parts.push(`<line class="dw-cut" x1="${x0 + w}" y1="${y0 - 14}" x2="${x0 + w}" y2="${y0 + h + 14}"/>`);
  for (const bar of bars) {
    parts.push(`<circle class="dw-bar" cx="${(x0 + bar.x * scale).toFixed(2)}"`
      + ` cy="${(y0 + (height - bar.y) * scale).toFixed(2)}" r="${Math.max(1.8, (bar.d / 2) * scale).toFixed(2)}"/>`);
  }
  // Ø10c/15cm is 6.67 bars a metre, and 5.24 cm²/m, but 7 bars are placed: mento counts both
  const label = (group) => [result.rebar[group], result.placed && t.bars_n.replace("{n}", result.placed[group]), result.area?.[group]]
    .filter(Boolean).join(" · ");
  if (result.rebar.top) parts.push(`<text class="dw-label" x="${x0}" y="${y0 - 10}">${label("top")}</text>`);
  if (result.rebar.bot) parts.push(`<text class="dw-label" x="${x0}" y="${y0 + h + 22}">${label("bot")}</text>`);
  // dimension lines: the thickness on the left, the width under the bottom label (its extension
  // lines start below the label, so they never cross it)
  parts.push(dimLeft(y0, y0 + h, x0, x0 - 14, `${fmt(height)} ${U.len}`),
    dimBelow(x0, x0 + w, y0 + h + 26, y0 + h + 38, `${fmt(width)} ${U.len}`));
  const description = t.fig_section.replace("{section}", note)
    .replace("{bars}", [result.rebar.bot, result.rebar.top].filter(Boolean).join(", "));
  return `<svg viewBox="0 0 ${box.w} ${box.h}" width="${box.w}" height="${box.h}" style="max-width:100%;height:auto"`
    + ` role="img" aria-label="${description}">${parts.join("")}</svg>`;
}

function python(state, result) {
  const concrete = CONCRETE_CLASS[state.code];
  const P = pyUnits(state.units);
  const lines = [
    `from mento import ${concrete}, SteelBar, OneWaySlab, Node, Forces`,
    P.imports,
    "",
    `concrete = ${concrete}(name="${state.code}", f_c=${P.fc(state.fc)})`,
    `steel = SteelBar(name="fy ${state.fy}", f_y=${P.fy(state.fy)})`,
    `slab = OneWaySlab(label="${state.label}", concrete=concrete, steel_bar=steel,`,
    `                  width=${P.len(num(state.width))}, height=${P.len(num(state.height))}, c_c=${P.cov(num(state.cover))})`,
    "forces = [",
    ...state.forces.filter((force) => num(force.M_y) || num(force.V_z) || num(force.N_x)).map((force) =>
      `    Forces(label="${force.label}", M_y=${P.moment(num(force.M_y) || 0)}, V_z=${P.force(num(force.V_z) || 0)},`
      + ` N_x=${P.force(num(force.N_x) || 0)}),`),
    "]",
    "node = Node(section=slab, forces=forces)",
    "",
  ];
  const layouts = state.mode === "check" ? { bot: mesh(state.rebar.bot), top: mesh(state.rebar.top) } : result?.layouts;
  if (state.mode === "design") {
    lines.push("# Let mento design the reinforcement", "node.design()");
  } else if (layouts) {
    lines.push("# Set the reinforcement");
    if (layouts.bot) lines.push(`slab.set_slab_longitudinal_rebar_bot(d_b1=${P.bar(layouts.bot.d)}, s_b1=${P.len(layouts.bot.s)})`);
    if (layouts.top) lines.push(`slab.set_slab_longitudinal_rebar_top(d_b1=${P.bar(layouts.top.d)}, s_b1=${P.len(layouts.top.s)})`);
  }
  return lines.concat([
    "",
    "# Perform all checks",
    "node.check()",
    "# Does it comply? The DCR is not all: mento lists the code limits the section misses",
    "print(slab.flexure_design.complies)",
    "for warning in node.warnings:",
    "    print(warning.code, warning.message)",
    "# Print results in Markdown format",
    "node.results",
    "# Print shear results in more detailed format in a DataFrame",
    "node.check_shear()",
    "# Print flexure results in more detailed format in a DataFrame",
    "node.check_flexure()",
    "# View detailed shear results",
    "node.shear_results_detailed()",
    "# View detailed flexure results",
    "node.flexure_results_detailed()",
  ]).join("\n");
}

const mesh = (face) => (Number(face.d) && Number(face.s) ? { d: Number(face.d), s: Number(face.s) } : null);

const barsFromLayouts = (layouts) => ({
  bot: { d: layouts.bot?.d || 0, s: layouts.bot?.s || 0 },
  top: { d: layouts.top?.d || 0, s: layouts.top?.s || 0 },
});

start({
  module: "slab",
  collection: { key: "slabs", fallback: "L1" },
  example: EXAMPLE,
  exampleUS: EXAMPLE_US,
  numbers: [
    { id: "width", unit: "strip" },
    { id: "height", unit: "len", rule: (state, number, U) => (number(state.height) <= 2 * U.cover(number(state.cover)) ? "err_height" : "") },
    { id: "cover", unit: "cov" },
  ],
  forces: ["M_y", "V_z", "N_x"],
  groups: [{ key: "bot", label: "bot" }, { key: "top", label: "top" }],
  bars: { bot_d: ["bot", "d"], bot_s: ["bot", "s"], top_d: ["top", "d"], top_s: ["top", "s"] },
  tables: ["flexure", "shear"],
  drawing,
  // the section as typed, before the first result: its bars come in with it
  preview: (state, U) => ({ section: { width: num(state.width), height: num(state.height), cover: U.cover(num(state.cover)), bars: [] }, rebar: {} }),
  python,
  barsFromLayouts,
});
