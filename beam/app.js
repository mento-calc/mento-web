// The rectangular beam: what makes this calculator different from the others.
// Everything it shares with them lives in shared/calculator.js.
import { CONCRETE_CLASS, num, pyUnits, start } from "../shared/calculator.js";
import { drawing } from "../shared/section.js";

const EXAMPLE = {
  code: "CIRSOC 201-25", fc: 25, fy: 420, width: 20, height: 60, cover: 25, label: "V-101", mode: "design",
  forces: [
    { label: "1.4D", M_y: 55, V_z: 45, N_x: 0 },
    { label: "1.2D+1.6L", M_y: 92, V_z: 70, N_x: 0 },
    { label: "0.9D+1.0E", M_y: -45, V_z: 65, N_x: 30 },
  ],
  rebar: {
    bot: { n1: 2, d1: 16, n2: 1, d2: 12, n3: 0, d3: 0, n4: 0, d4: 0 },
    top: { n1: 2, d1: 12, n2: 0, d2: 0, n3: 0, d3: 0, n4: 0, d4: 0 },
    stirrups: { n: 2, d: 6, s: 13 },  // n counts legs, as mento does: one closed stirrup is two
  },
  choice: {},
};
// The same beam for the US (py/beam.py EXAMPLE_US): inches, psi, Grade 60, kip-ft, ASTM bar sizes.
const EXAMPLE_US = {
  code: "ACI 318-19", fc: 4000, fy: 60, width: 12, height: 24, cover: 1.5, label: "B-101", mode: "design",
  forces: [
    { label: "1.4D", M_y: 45, V_z: 20, N_x: 0 },
    { label: "1.2D+1.6L", M_y: 72, V_z: 30, N_x: 0 },
    { label: "0.9D+1.0E", M_y: -58, V_z: 26, N_x: 7 },
  ],
  rebar: {
    bot: { n1: 2, d1: 6, n2: 0, d2: 0, n3: 0, d3: 0, n4: 0, d4: 0 },
    top: { n1: 2, d1: 5, n2: 1, d2: 4, n3: 0, d3: 0, n4: 0, d4: 0 },
    stirrups: { n: 2, d: 3, s: 10 },
  },
  choice: {},
};


// What the cage carries besides the bars the checks count, under the rebar in both modes: the skin
// of each side face, and the mounting bars of a face. mento lays both out; neither adds resistance.
function extras(result, t) {
  const row = (label, bars, where, why) => `<div class="xtra"><span class="lbl">${label}</span>`
    + `<span class="val">${bars} <span class="why">${where} · ${why}</span></span></div>`;
  const rows = [];
  if (result.skin) rows.push(row(t.skin, result.skin.bars, t.per_side, t[`skin_${result.skin.status}`] ?? t.skin_proposed));
  for (const item of result.mounting || []) rows.push(row(t.mounting, item.bars, t[`at_${item.face}`], t.mounting_why));
  return rows.join("");
}

const face = (layout, P) => Object.keys(layout).filter((key) => key.startsWith("n"))
  .map((key) => `n${key.slice(1)}=${layout[key]}, d_b${key.slice(1)}=${P.bar(layout[`d${key.slice(1)}`])}`).join(", ");

function python(state, result) {
  const concrete = CONCRETE_CLASS[state.code];
  const P = pyUnits(state.units);
  const lines = [
    `from mento import ${concrete}, SteelBar, RectangularBeam, Node, Forces`,
    P.imports,
    "",
    `concrete = ${concrete}(name="${state.code}", f_c=${P.fc(state.fc)})`,
    `steel = SteelBar(name="fy ${state.fy}", f_y=${P.fy(state.fy)})`,
    `beam = RectangularBeam(label="${state.label}", concrete=concrete, steel_bar=steel,`,
    `                       width=${P.len(num(state.width))}, height=${P.len(num(state.height))}, c_c=${P.cov(num(state.cover))})`,
    "forces = [",
    ...state.forces.filter((force) => num(force.M_y) || num(force.V_z)).map((force) =>
      `    Forces(label="${force.label}", M_y=${P.moment(num(force.M_y) || 0)}, V_z=${P.force(num(force.V_z) || 0)},`
      + ` N_x=${P.force(num(force.N_x) || 0)}),`),
    "]",
    "node = Node(section=beam, forces=forces)",
    "",
  ];
  const layouts = state.mode === "check"
    ? { bot: toLayout(state.rebar.bot), top: toLayout(state.rebar.top), st: Number(state.rebar.stirrups.n) ? state.rebar.stirrups : null }
    : result?.layouts;
  if (state.mode === "design") {
    lines.push("# Let mento design the reinforcement", "node.design()");
  } else if (layouts) {
    lines.push("# Set the reinforcement");
    if (layouts.bot) lines.push(`beam.set_longitudinal_rebar_bot(${face(layouts.bot, P)})`);
    // a new beam starts with 2Ø8 on each face: an empty top face has to be said
    lines.push(layouts.top && Object.keys(layouts.top).length
      ? `beam.set_longitudinal_rebar_top(${face(layouts.top, P)})`
      : "beam.set_longitudinal_rebar_top(n1=0, d_b1=None)  # no bars on top");
    if (layouts.st?.n) {
      lines.push(`beam.set_transverse_rebar(n_legs=${layouts.st.n}, d_b=${P.bar(layouts.st.d)}, s_l=${P.len(layouts.st.s)})`);
    }
  }
  return lines.concat([
    "",
    "# Perform all checks",
    "node.check()",
    "# Does it comply? The DCR is not all: mento lists the code limits the section misses",
    "print(beam.flexure_design.complies)",
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
    "# Draw the section: its cage, with the mounting bars and the skin of a deep beam",
    "beam.plot()",
  ]).join("\n");
}

// A face is four groups, as mento numbers them: n1 + n2 in the first row, n3 + n4 in the second.
const GROUPS = [1, 2, 3, 4];

const toLayout = (face_) => {
  const layout = {};
  for (const index of GROUPS) {
    if (Number(face_[`n${index}`])) {
      layout[`n${index}`] = Number(face_[`n${index}`]);
      layout[`d${index}`] = Number(face_[`d${index}`]);
    }
  }
  return layout;
};

// The design into the check fields, each group in its own field and row.
function barsFromLayouts(layouts) {
  const face = (layout) => Object.fromEntries(GROUPS.flatMap((index) => [
    [`n${index}`, layout?.[`n${index}`] || 0], [`d${index}`, layout?.[`n${index}`] ? layout[`d${index}`] : 0],
  ]));
  return {
    bot: face(layouts.bot),
    top: face(layouts.top),
    stirrups: { n: layouts.st?.n || 0, d: layouts.st?.d || 0, s: layouts.st?.s || 0 },
  };
}

start({
  module: "beam",
  collection: { key: "beams", fallback: "B1" },
  example: EXAMPLE,
  exampleUS: EXAMPLE_US,
  numbers: [
    { id: "width", unit: "len" },
    { id: "height", unit: "len", rule: (state, number, U) => (number(state.height) <= 2 * U.cover(number(state.cover)) ? "err_height" : "") },
    { id: "cover", unit: "cov" },
  ],
  forces: ["M_y", "V_z", "N_x"],
  groups: [{ key: "bot", label: "bot" }, { key: "top", label: "top" }, { key: "st", label: "st" }],
  // the perimeter stirrup is closed: it has two legs, and a leg alone is no stirrup
  barRules: [{ id: "st_n", error: "err-st", rule: (state, number) => (number(state.rebar.stirrups.n) === 1 ? "err_legs" : "") }],
  // A link from before the stirrups were counted by their legs: each closed stirrup is two.
  fromV1: (state, carried) => {
    if (carried.bars) state.rebar.stirrups.n = 2 * Number(state.rebar.stirrups.n);
    state.choice.st = (state.choice.st || "").replace(/^\d+(?=x)/, (count) => 2 * count);
  },
  bars: {
    bot_n1: ["bot", "n1"], bot_d1: ["bot", "d1"], bot_n2: ["bot", "n2"], bot_d2: ["bot", "d2"],
    top_n1: ["top", "n1"], top_d1: ["top", "d1"], top_n2: ["top", "n2"], top_d2: ["top", "d2"],
    st_n: ["stirrups", "n"], st_d: ["stirrups", "d"], st_s: ["stirrups", "s"],
    // the second rows come last, so a link from before they existed still reads its first eleven
    bot_n3: ["bot", "n3"], bot_d3: ["bot", "d3"], bot_n4: ["bot", "n4"], bot_d4: ["bot", "d4"],
    top_n3: ["top", "n3"], top_d3: ["top", "d3"], top_n4: ["top", "n4"], top_d4: ["top", "d4"],
  },
  tables: ["flexure", "shear"],
  drawing,
  // the section as typed, before the first result: no bars yet, the stirrup still dashed
  preview: (state, U) => ({
    section: {
      width: num(state.width), height: num(state.height), cover: U.cover(num(state.cover)),
      stirrup: { d: 0, bend: 0 }, closed: [], ties: [], bars: [], labels: [],
    },
    rebar: {},
  }),
  extras,
  python,
  barsFromLayouts,
});
