// The continuous beam: spans between column axes, the columns that hold it, loads by case in; the
// beam solved and each span designed by mento out (py/continuous.py). Its inputs are lists, not
// one section, so it keeps its own state and wiring; what it shares with the other calculators
// comes from shared/.
import { $, CONCRETE_CLASS, MATERIALS, MATERIALS_US, dcrState, fmt, num, pyUnits, track, two } from "../shared/calculator.js";
import { UNIT_WORDS, applyStrings, loadStrings, preferredLang, preferredUnits, rememberLang, rememberUnits } from "../shared/i18n.js";
import { copy, escapeHtml, foldable, highlightPython, numberField, radiogroup, toast, tween } from "../shared/ui.js";

const MODULE = "continuous";
// py/continuous.py EXAMPLE and EXAMPLE_US, in the page's shape. The metric one is the beam the page
// is checked against CYPECAD with (py/test_cypecad.py).
const loadsOn = (sdl, live) => ["SDL", "L"].flatMap((kind) => [0, 1, 2].map((i) => (
  { case: kind, type: "uniform", on: `s${i}`, value: kind === "SDL" ? sdl : live, a: "", b: "" })));
const columnsOf = (widths, depth, height) => widths.map((a) => ({ a, b: depth, Hb: height, Ha: height, pinned: false }));
const EXAMPLE = {
  code: "EN 1992-2004", fc: 25, fy: 500, cover: 30, redis: 15, pattern: false, defl: 3, head: 0.3, label: "V-101",
  base: "pinned", above: "last",
  spans: [{ L: 5, b: 30, h: 50 }, { L: 4, b: 30, h: 50 }, { L: 3, b: 30, h: 50 }],
  columns: columnsOf([30, 50, 40, 30], 30, 3),
  ends: { left: { type: "column", L: 1.5, b: 30, h: 50 }, right: { type: "column", L: 1.5, b: 30, h: 50 } },
  loads: loadsOn(6, 4),
};
const EXAMPLE_US = {
  code: "ACI 318-19", fc: 4000, fy: 60, cover: 1.5, redis: 15, pattern: false, defl: 3, head: 0.3, label: "B-101",
  base: "pinned", above: "last",
  spans: [{ L: 16, b: 12, h: 20 }, { L: 13, b: 12, h: 20 }, { L: 10, b: 12, h: 20 }],
  columns: columnsOf([12, 20, 16, 12], 12, 10),
  ends: { left: { type: "column", L: 5, b: 12, h: 20 }, right: { type: "column", L: 5, b: 12, h: 20 } },
  loads: loadsOn(0.5, 0.5),
};
const DEFAULT_MATERIALS = { "CIRSOC 201-25": [25, 420], "ACI 318-19": [25, 420], "EN 1992-2004": [25, 500] };
const DEFAULT_MATERIALS_US = { "ACI 318-19": [4000, 60] };
const materialsFor = (units) => (units === "us" ? MATERIALS_US : MATERIALS);
const MAX_SPANS = 8;
const MAX_LOADS = 24;
const HASH_VERSION = "3";
const SPAN = { si: "m", us: "ft" };
const LINE = { si: "kN/m", us: "kip/ft" };
const SMALL = { si: "mm", us: "in" };
const CHARTS = ["M", "V", "v", "As", "Av", "rebar"];

const strings = await loadStrings(MODULE);
let lang = new URLSearchParams(location.hash.slice(1)).get("l") || preferredLang();
const state = readHash() || exampleFor(preferredUnits());
let t = applyStrings(strings, lang, document, state.units);
let metricCode = state.units === "si" ? state.code : null;
let lastResult = null;   // the last full answer: analysis and design
let lastAnalysis = null; // the last quick one, the diagrams alone
let shown = new Set(["M", "V", "rebar"]);
try { const saved = JSON.parse(localStorage.getItem(`mento-${MODULE}-charts`)); if (Array.isArray(saved)) shown = new Set(saved); } catch { /* storage blocked */ }

function exampleFor(units) {
  return { units, ...structuredClone(units === "us" ? EXAMPLE_US : EXAMPLE) };
}

// The cases by the code's names: D, SDL and L, or under EN 1992 PP, CM and SCU (EHE-08 words).
// py/continuous.py COMBINATIONS: each ultimate combination as (factor of D + SDL, factor of L);
// py/test_site.py holds the two in step.
const COMBINATIONS = {"ACI 318-19": [[1.4, 0], [1.2, 1.6]], "CIRSOC 201-25": [[1.4, 0], [1.2, 1.6]], "EN 1992-2004": [[1.0, 0], [1.35, 0], [1.0, 1.5], [1.35, 1.5]]};
const caseName = (kind) => (state.code === "EN 1992-2004" ? { D: "PP", SDL: "CM", L: "SCU" } : { D: "D", SDL: "SDL", L: "L" })[kind];

// ------------------------------------------------------------ the beam's elements
// A load sits on an element by key, so that adding an overhang does not move it: "s0".."s7" the
// spans, "ol" and "or" the overhangs. The payload numbers them as the glue does, left to right.
function elements() {
  const found = [];
  if (state.ends.left.type === "overhang") found.push({ key: "ol", kind: "left", ...state.ends.left, name: t.overhang_left });
  state.spans.forEach((span, index) => found.push({ key: `s${index}`, kind: "span", ...span, name: t.span_n.replace("{n}", index + 1) }));
  if (state.ends.right.type === "overhang") found.push({ key: "or", kind: "right", ...state.ends.right, name: t.overhang_right });
  return found;
}

function payload() {
  const list = elements();
  const index = Object.fromEntries(list.map((element, position) => [element.key, position]));
  const end = (side) => {
    const value = state.ends[side];
    return value.type === "overhang" ? { type: "overhang", L: num(value.L), width: num(value.b), height: num(value.h) } : { type: "column" };
  };
  const position = (value) => (String(value ?? "").trim() === "" ? null : num(value));
  return {
    code: state.code, units: state.units, lang, label: state.label, fc: state.fc, fy: state.fy,
    cover: num(state.cover), redistribution: num(state.redis), self_weight: true, pattern: Boolean(state.pattern),
    deflection_factor: num(state.defl), head_fixity: num(state.head), base: state.base, above: state.above,
    spans: state.spans.map((span) => ({ L: num(span.L), width: num(span.b), height: num(span.h) })),
    columns: state.columns.map((column) => ({
      width: num(column.a), depth: num(column.b), H_below: num(column.Hb) || 0, H_above: num(column.Ha) || 0, pinned: Boolean(column.pinned),
    })),
    ends: { left: end("left"), right: end("right") },
    loads: state.loads.filter((load) => load.on in index).map((load) => ({
      case: load.case, type: load.type, element: index[load.on], value: num(load.value),
      ...(load.type === "point" ? { a: position(load.a) ?? 0 } : { a: position(load.a), b: position(load.b) }),
    })),
  };
}

// ------------------------------------------------------------ state in the URL
function readHash() {
  if (!location.hash.startsWith("#v")) return null;
  try {
    const params = new URLSearchParams(location.hash.slice(1));
    if (params.get("v") !== HASH_VERSION) return null;
    const saved = JSON.parse(params.get("s"));
    const units = saved.units === "us" ? "us" : "si";
    const next = { ...exampleFor(units), ...saved, units };
    if (!materialsFor(units)[next.code]) next.code = exampleFor(units).code;
    if (!Array.isArray(next.spans) || !next.spans.length || next.columns?.length !== next.spans.length + 1) return null;
    return next;
  } catch {
    return null;
  }
}

function writeHash() {
  const params = new URLSearchParams({ v: HASH_VERSION, l: lang, s: JSON.stringify(state) });
  history.replaceState(null, "", `${location.pathname}#${params}`);
}

// ------------------------------------------------------------ units
const round = (value, size) => Number((Math.round(value / size) * size).toFixed(3));
const nearest = (options, value) => options.reduce((a, b) => (Math.abs(b - value) < Math.abs(a - value) ? b : a));
// Each kind converts [to US, to SI], rounded to what a drawing in that system would say.
const CONVERT = {
  span: [(v) => round(v / 0.3048, 0.5), (v) => round(v * 0.3048, 0.05)],
  len: [(v) => round(v / 2.54, 1), (v) => round(v * 2.54, 5)],
  cov: [(v) => round(v / 25.4, 0.25), (v) => round(v * 25.4, 5)],
  line: [(v) => round(v * 0.0685218, 0.01), (v) => round(v / 0.0685218, 0.1)],
  force: [(v) => round(v * 0.224809, 0.1), (v) => round(v / 0.224809, 0.1)],
};
let unitsUndo = null;

function convertState(next) {
  const way = next === "us" ? 0 : 1;
  const by = (kind, value) => (String(value ?? "").trim() === "" || !Number.isFinite(num(value)) ? value : CONVERT[kind][way](num(value)));
  const fc = next === "us" ? num(state.fc) * 145.038 : num(state.fc) / 145.038;
  const fy = next === "us" ? num(state.fy) / 6.89476 : num(state.fy) * 6.89476;
  if (next === "us") metricCode = state.code;
  state.code = next === "us" ? "ACI 318-19" : metricCode || state.code;
  const materials = materialsFor(next)[state.code];
  state.fc = nearest(materials.concrete.map(([value]) => value), fc);
  state.fy = nearest(materials.steel.map(([value]) => value), fy);
  state.cover = by("cov", state.cover);
  for (const element of [...state.spans, state.ends.left, state.ends.right]) {
    element.L = by("span", element.L);
    element.b = by("len", element.b);
    element.h = by("len", element.h);
  }
  for (const column of state.columns) {
    column.a = by("len", column.a);
    column.b = by("len", column.b);
    column.Hb = by("span", column.Hb);
    column.Ha = by("span", column.Ha);
  }
  for (const load of state.loads) {
    load.value = by(load.type === "point" ? "force" : "line", load.value);
    load.a = by("span", load.a);
    load.b = by("span", load.b);
  }
}

function setUnits(next) {
  if (next === state.units) return;
  const before = structuredClone(state);
  if (unitsUndo?.units === next && unitsUndo.produced === JSON.stringify(state)) {
    for (const key of Object.keys(state)) delete state[key];
    Object.assign(state, structuredClone(unitsUndo.state));
    metricCode = next === "si" ? state.code : metricCode;
  } else {
    convertState(next);
    state.units = next;
  }
  unitsUndo = { units: before.units, state: before, produced: JSON.stringify(state) };
  rememberUnits(next);
  t = applyStrings(strings, lang, document, state.units);
  renderInputs();
  schedule({ now: true });
}

// ------------------------------------------------------------ worker
const workerUrl = `../shared/worker.js?module=${MODULE}`;
await fetch(workerUrl, { cache: "no-cache" }).catch(() => {});
const worker = new Worker(workerUrl);
const pending = new Map();
let nextId = 1;
let ready = false;
let version = 0;  // the edit a result must match to be applied

function call(type, message) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, type, payload: message });
  });
}

worker.onmessage = ({ data }) => {
  if (data.type === "progress") return showStep(data.step);
  if (data.type === "ready") {
    ready = true;
    track("mento_ready", { calculator: MODULE, load_seconds: Math.round(performance.now() / 100) / 10 });
    $("version").textContent = data.version;
    $("step-mento").textContent = `mento ${data.version}`;
    return calculate();
  }
  if (data.type === "fatal") return showFatal(data.error);
  const entry = pending.get(data.id);
  if (!entry) return;
  pending.delete(data.id);
  data.ok ? entry.resolve(data.result) : entry.reject(new Error(data.error));
};

const STEPS = ["python", "packages", "mento", "warmup"];
function showStep(step) {
  const reached = STEPS.indexOf(step);
  for (const item of $("steps").children) {
    const index = STEPS.indexOf(item.dataset.step);
    item.dataset.s = index < reached ? "done" : index === reached ? "doing" : "todo";
    item.querySelector(".status").textContent = index < reached ? t.ready : index === reached ? t.working : "";
    item.querySelector(".dot").innerHTML = index < reached ? '<svg width="10" height="10" style="color:#fff"><use href="#i-check"/></svg>' : "";
  }
  $("progress").style.width = `${8 + 23 * (reached + 1)}%`;
}

function showFatal(message) {
  $("loading").hidden = true;
  $("output").hidden = false;
  $("verdict").hidden = true;
  $("notices").innerHTML = notice("bad", `${escapeHtml(t.fatal)} <span class="mono">${escapeHtml(message)}</span>`);
}

// ------------------------------------------------------------ recalculation
// Two answers per edit: the analysis, in milliseconds, redraws the diagrams at once; the design of
// every span follows. One run at a time: edits made meanwhile go as one run of the latest inputs.
let timer = 0;
let running = false;
let queued = false;
let calculated = false;
const inputsOf = (sent) => JSON.stringify({ ...sent, lang: null, label: null });
let openingInputs = null;
const context = () => ({ calculator: MODULE, code: state.code, units: state.units });

function schedule({ now = false } = {}) {
  version += 1;
  renderPython();
  renderDrawing();
  if (!validate()) return;
  writeHash();
  if (!ready) return markStale(t.recalculating);
  clearTimeout(timer);
  timer = setTimeout(calculate, now ? 0 : 250);
}

async function calculate() {
  if (running) { queued = true; return; }
  running = true;
  try {
    await calculateOnce();
  } finally {
    running = false;
    if (queued) { queued = false; calculate(); }
  }
}

async function ask(type, sent) {
  try {
    return await call(type, sent);
  } catch (error) {
    return { ok: false, kind: "mento", message: error.message };
  }
}

async function calculateOnce() {
  const mine = version;
  const sent = payload();
  $("result").classList.add("is-computing");
  const quick = await ask("analyse", sent);
  if (mine !== version) return;
  if (!quick.ok) { $("result").classList.remove("is-computing"); return renderError(quick); }
  lastAnalysis = quick;
  renderAnalysis(quick);
  if (lastResult) markStale(t.designing);
  const full = await ask("run", sent);
  if (mine !== version) return;
  $("result").classList.remove("is-computing");
  if (!full.ok) return renderError(full);
  render(full);
  saveResult(sent, full);
  if (!calculated && openingInputs !== null && inputsOf(sent) !== openingInputs) {
    calculated = true;
    track("calculate", { ...context(), verdict: full.complies ? "ok" : "bad" });
  }
}

// The last result, next to the payload it answers: a visit that asks the same shows it at once.
const SAVED_KEY = `mento-${MODULE}-result`;
function saveResult(sent, result) {
  try { localStorage.setItem(SAVED_KEY, JSON.stringify({ payload: JSON.stringify(sent), result })); } catch { /* storage blocked */ }
}
function showSaved() {
  try {
    const saved = JSON.parse(localStorage.getItem(SAVED_KEY));
    const current = saved?.result?.ok && saved.result.version === $("version").textContent;
    if (saved?.payload !== JSON.stringify(payload()) || !current) return;
    render(saved.result);
  } catch {
    try { localStorage.removeItem(SAVED_KEY); } catch { /* storage blocked */ }
  }
}

// ------------------------------------------------------------ validation
function fieldError(id, message) {
  $(`field-${id}`).classList.toggle("is-invalid", Boolean(message));
  $(`err-${id}`).hidden = !message;
  $(`err-${id}`).textContent = message;
}

function validate() {
  const filled = (value) => String(value ?? "").trim() !== "" && Number.isFinite(num(value));
  const positive = (value) => filled(value) && num(value) > 0;
  const checks = {
    cover: positive(state.cover) ? "" : t.err_positive,
    redis: filled(state.redis) && num(state.redis) >= 0 && num(state.redis) <= 30 ? "" : t.err_redis,
    defl: filled(state.defl) && num(state.defl) >= 1 ? "" : t.err_defl,
    head: filled(state.head) && num(state.head) >= 0 && num(state.head) <= 1 ? "" : t.err_head,
  };
  for (const [id, message] of Object.entries(checks)) fieldError(id, message);
  let ok = Object.values(checks).every((message) => !message);
  for (const cell of document.querySelectorAll("#spans .cell, #columns .cell")) {
    const input = cell.querySelector("input");
    const optional = input.dataset.k === "Hb" || input.dataset.k === "Ha";
    const bad = optional ? !(filled(input.value) && num(input.value) >= 0) : !positive(input.value);
    cell.classList.toggle("is-invalid", bad);
    ok = ok && !bad;
  }
  const lengths = Object.fromEntries(elements().map((element) => [element.key, num(element.L)]));
  for (const row of $("loads").querySelectorAll(".cb-load")) {
    const load = state.loads[Number(row.dataset.index)];
    const value = row.querySelector('[data-k="value"]');
    const badValue = !filled(load.value);
    value.classList.toggle("is-invalid", badValue);
    ok = ok && !badValue;
    for (const key of ["a", "b"]) {
      const cell = row.querySelector(`[data-k="${key}"]`);
      if (!cell) continue;
      const raw = String(load[key] ?? "").trim();
      const bad = raw !== "" && !(num(raw) >= 0 && num(raw) <= (lengths[load.on] ?? Infinity) + 1e-9);
      cell.classList.toggle("is-invalid", bad);
      ok = ok && !bad;
    }
  }
  if (!ok) showInvalid();
  return ok;
}

function showInvalid() {
  if (!lastResult) return;
  const strip = $("strip-info");
  strip.hidden = false;
  strip.className = "strip strip--bad";
  strip.innerHTML = `<span>${escapeHtml(t.fix_input)}</span>`;
  $("verdict").classList.add("is-stale");
}

function markStale(text) {
  if (!lastResult) return;
  const strip = $("strip-info");
  strip.hidden = false;
  strip.className = "strip strip--info";
  strip.innerHTML = `<span class="spin"></span><span>${escapeHtml(text)}</span>`;
  $("verdict").classList.add("is-stale");
  $("ledger").classList.add("is-stale");
}

function clearStale() {
  $("strip-info").hidden = true;
  $("verdict").classList.remove("is-stale");
  $("ledger").classList.remove("is-stale");
}

// ------------------------------------------------------------ station 1
function fillSelect(select, options, value) {
  select.innerHTML = options.map(([option, text]) => `<option value="${option}"${String(option) === String(value) ? " selected" : ""}>${escapeHtml(text)}</option>`).join("");
}

function renderMaterials() {
  const codes = Object.keys(materialsFor(state.units));
  fillSelect($("code"), codes.map((code) => [code, code === "EN 1992-2004" ? "EN 1992" : code]), state.code);
  const materials = materialsFor(state.units)[state.code];
  fillSelect($("fc"), materials.concrete, state.fc);
  fillSelect($("fy"), materials.steel, state.fy);
}

function renderUnits() {
  document.querySelectorAll("[data-unit]").forEach((element) => { element.textContent = UNIT_WORDS[state.units][element.dataset.unit]; });
  document.querySelectorAll("#units [data-units]").forEach((button) => button.setAttribute("aria-checked", String(button.dataset.units === state.units)));
  $("units-note").hidden = state.units !== "us";
}

// ------------------------------------------------------------ station 2: spans, columns, ends
const input = (key, value, aria) => `<span class="cell"><input type="number" inputmode="decimal" step="any" data-k="${key}"`
  + ` aria-label="${escapeHtml(aria)}" value="${escapeHtml(value ?? "")}"></span>`;

function renderSpans() {
  $("spans").innerHTML = elements().map((element) => {
    const aria = (key) => t[key].replace("{name}", element.name);
    const remove = element.kind === "span"
      ? `<button type="button" class="rm" aria-label="${escapeHtml(t.remove)}"${state.spans.length === 1 ? " disabled" : ""}>×</button>` : "<span></span>";
    return `<div class="cb-row${element.kind === "span" ? "" : " cb-row--overhang"}" data-key="${element.key}">`
      + `<span class="name">${escapeHtml(element.name)}</span>${input("L", element.L, aria("aria_L"))}${input("b", element.b, aria("aria_b"))}`
      + `${input("h", element.h, aria("aria_h"))}${remove}</div>`;
  }).join("");
  $("columns").innerHTML = state.columns.map((column, index) => {
    const name = t.column_n.replace("{name}", String.fromCharCode(65 + index));
    const aria = (key) => t[key].replace("{name}", name);
    return `<div class="cb-row cb-row--col" data-index="${index}"><span class="name">${escapeHtml(name)}</span>`
      + `${input("a", column.a, aria("aria_col_a"))}${input("b", column.b, aria("aria_col_b"))}`
      + `${input("Hb", column.Hb, aria("aria_col_Hb"))}${input("Ha", column.Ha, aria("aria_col_Ha"))}`
      + `<input type="checkbox" data-k="pinned" aria-label="${escapeHtml(aria("aria_col_pin"))}"${column.pinned ? " checked" : ""}></div>`;
  }).join("");
  $("add-span").disabled = state.spans.length >= MAX_SPANS;
  for (const side of ["left", "right"]) fillSelect($(`end-${side}`), [["column", t.end_column], ["overhang", t.end_overhang]], state.ends[side].type);
  fillSelect($("base"), [["pinned", t.base_pinned], ["fixed", t.base_fixed], ["floor", t.base_floor]], state.base);
  fillSelect($("above"), [["last", t.above_last], ["intermediate", t.above_mid]], state.above);
}

function elementOf(key) {
  if (key === "ol") return state.ends.left;
  if (key === "or") return state.ends.right;
  return state.spans[Number(key.slice(1))];
}

$("spans").addEventListener("input", (event) => {
  const field = event.target.closest("input");
  if (!field) return;
  elementOf(field.closest(".cb-row").dataset.key)[field.dataset.k] = field.value;
  schedule();
});
$("columns").addEventListener("input", (event) => {
  const field = event.target.closest("input");
  if (!field) return;
  const column = state.columns[Number(field.closest(".cb-row").dataset.index)];
  column[field.dataset.k] = field.type === "checkbox" ? field.checked : field.value;
  schedule({ now: field.type === "checkbox" });
});
$("spans").addEventListener("click", (event) => {
  const button = event.target.closest(".rm");
  if (!button || button.disabled) return;
  const index = Number(button.closest(".cb-row").dataset.key.slice(1));
  state.spans.splice(index, 1);
  state.columns.splice(index + 1, 1);
  // the loads of the span go with it; those of the spans past it move one back
  state.loads = state.loads.filter((load) => load.on !== `s${index}`)
    .map((load) => (load.on.startsWith("s") && Number(load.on.slice(1)) > index ? { ...load, on: `s${Number(load.on.slice(1)) - 1}` } : load));
  renderSpans();
  renderLoads();
  schedule({ now: true });
});
$("add-span").addEventListener("click", () => {
  if (state.spans.length >= MAX_SPANS) return;
  state.spans.push({ ...state.spans.at(-1) });
  state.columns.push({ ...state.columns.at(-1) });
  const index = state.spans.length - 1;
  // a new span carries the loads of the one before it, as a beam of equal spans would
  const copied = state.loads.filter((load) => load.on === `s${index - 1}`).map((load) => ({ ...load, on: `s${index}` }));
  state.loads.push(...copied.slice(0, MAX_LOADS - state.loads.length));
  renderSpans();
  renderLoads();
  schedule({ now: true });
});
for (const side of ["left", "right"]) {
  $(`end-${side}`).addEventListener("change", () => {
    state.ends[side].type = $(`end-${side}`).value;
    renderSpans();
    renderLoads();
    schedule({ now: true });
  });
}

// ------------------------------------------------------------ station 3: loads, one per line
// The combinations the code brings, written out: what the envelope is made of, in the names of the
// load cases the page uses.
function renderCombos() {
  const permanent = `(${caseName("D")} + ${caseName("SDL")})`;
  const term = (factor, name) => `${factor.toFixed(2)}·${name}`;
  $("combos").innerHTML = (COMBINATIONS[state.code] || []).map(([dead, live]) => `<li class="mono">${escapeHtml(
    [term(dead, permanent), live ? term(live, caseName("L")) : ""].filter(Boolean).join(" + "))}</li>`).join("");
  const service = `${caseName("D")} + ${caseName("SDL")} + ${caseName("L")}`;
  $("combos-hint").textContent = [t.combos_hint.replace("{service}", service),
    state.pattern ? t.combos_pattern.replace("{live}", caseName("L")) : ""].filter(Boolean).join(" ");
}

function renderLoads() {
  renderCombos();
  const list = elements();
  const head = `<div class="cb-loads-head" aria-hidden="true"><span class="lbl">${escapeHtml(t.case)}</span><span class="lbl">${escapeHtml(t.type)}</span>`
    + `<span class="lbl">${escapeHtml(t.where)}</span><span class="lbl">${escapeHtml(t.value)}</span><span class="lbl">${escapeHtml(t.from)}</span>`
    + `<span class="lbl">${escapeHtml(t.to)}</span><span></span></div>`;
  $("loads").innerHTML = head + state.loads.map((load, index) => {
    const n = index + 1;
    const aria = (key) => escapeHtml(t[key].replace("{n}", n));
    const select = (key, options, value, extra = "") => `<span class="cell${extra}"><select data-k="${key}" aria-label="${aria(`aria_${key}`)}">`
      + options.map(([option, text]) => `<option value="${option}"${option === value ? " selected" : ""}>${escapeHtml(text)}</option>`).join("") + "</select></span>";
    const field = (key, ariaKey, placeholder, unit = "") => `<span class="cell" data-k="${key}"><input type="number" inputmode="decimal" step="any" data-k="${key}"`
      + ` aria-label="${aria(ariaKey)}" placeholder="${escapeHtml(placeholder)}" value="${escapeHtml(load[key] ?? "")}">${unit ? `<span class="u">${unit}</span>` : ""}</span>`;
    const unit = load.type === "point" ? UNIT_WORDS[state.units].force : LINE[state.units];
    const L = num(elementOf(load.on)?.L);
    const where = load.type === "point"
      ? `${field("a", "aria_at", t.at_placeholder, SPAN[state.units])}<span></span>`
      : `${field("a", "aria_a", "0", SPAN[state.units])}${field("b", "aria_b_load", Number.isFinite(L) ? fmt(L) : "", SPAN[state.units])}`;
    return `<div class="cb-load" data-index="${index}">`
      + select("case", ["D", "SDL", "L"].map((kind) => [kind, caseName(kind)]), load.case, load.case === "L" ? " case-L" : "")
      + select("type", [["uniform", t.type_uniform], ["point", t.type_point]], load.type)
      + select("on", list.map((element) => [element.key, element.name]), load.on)
      + `<button type="button" class="rm" aria-label="${escapeHtml(t.remove)}">×</button>`
      + `<span class="wide">${field("value", "aria_value", "", unit)}</span>${where}</div>`;
  }).join("");
  // the remove button sits last on a desktop line: the grid order puts it there
  for (const row of $("loads").querySelectorAll(".cb-load")) row.append(row.querySelector(".rm"));
  $("add-load").disabled = state.loads.length >= MAX_LOADS;
}

$("loads").addEventListener("input", (event) => {
  const field = event.target.closest("input, select");
  if (!field) return;
  const load = state.loads[Number(field.closest(".cb-load").dataset.index)];
  load[field.dataset.k] = field.value;
  if (field.tagName === "SELECT") {
    if (field.dataset.k === "type") { load.a = ""; load.b = ""; }
    renderLoads();
    schedule({ now: true });
  } else {
    schedule();
  }
});
$("loads").addEventListener("click", (event) => {
  const button = event.target.closest(".rm");
  if (!button) return;
  state.loads.splice(Number(button.closest(".cb-load").dataset.index), 1);
  renderLoads();
  schedule({ now: true });
});
$("add-load").addEventListener("click", () => {
  if (state.loads.length >= MAX_LOADS) return;
  state.loads.push({ case: "L", type: "point", on: elements().find((element) => element.kind === "span").key, value: state.units === "us" ? 2 : 10, a: "", b: "" });
  renderLoads();
  schedule({ now: true });
});

// ------------------------------------------------------------ drawing frame
// Every drawing of the beam shares one horizontal scale: the beam to scale between the outer
// faces of its end columns (or the tips of its overhangs), its depth exaggerated.
const phone = () => window.matchMedia("(max-width: 720px)").matches;
function frame() {
  const list = elements();
  const lengths = list.map((element) => num(element.L));
  if (!lengths.every((value) => Number.isFinite(value) && value > 0)) return null;
  const nodes = [0];
  for (const length of lengths) nodes.push(nodes.at(-1) + length);
  // the columns sit on the nodes that are not an overhang's tip
  const held = nodes.map((_, j) => !(j === 0 && list[0].kind === "left") && !(j === nodes.length - 1 && list.at(-1).kind === "right"));
  const columnAt = [];
  let c = 0;
  held.forEach((isHeld, j) => { columnAt[j] = isHeld ? state.columns[c++] : null; });
  const toSpan = state.units === "us" ? 1 / 12 : 1 / 100;
  const half = columnAt.map((column) => (column ? (num(column.a) || 0) * toSpan / 2 : 0));
  const x0 = -half[0];
  const x1 = nodes.at(-1) + half.at(-1);
  const small = phone();
  const W = small ? 343 : 860;
  const [left, right] = [small ? 16 : 30, small ? 12 : 20];
  const sx = (W - left - right) / (x1 - x0);
  const X = (x) => left + (x - x0) * sx;
  return { list, lengths, nodes, columnAt, half, x0, x1, W, X, sx, small, fs: small ? 9.5 : 11, left, toSpan };
}
const px = (value) => value.toFixed(1);
function svgText(parts, f) {
  return (x, y, value, cls = "el-t", anchor = "middle") => parts.push(`<text class="${cls}" x="${px(x)}" y="${px(y)}" text-anchor="${anchor}" style="font-size:${f.fs}px">${escapeHtml(value)}</text>`);
}

// The beam with its geometry and loads: beam and columns one grey body, the columns as wide as
// they are; each load case a band of its own colour as tall as its intensity, its value and unit on
// it and its name in the legend under the drawing; the own weight drawn too.
function renderDrawing() {
  const f = frame();
  if (!f) { $("drawing").innerHTML = ""; return; }
  const { list, lengths, nodes, columnAt, X, W, small, toSpan } = f;
  const parts = [];
  const text = svgText(parts, f);
  const depths = list.map((element) => num(element.h));
  const deepest = Math.max(...depths.filter(Number.isFinite), 1);
  const thick = depths.map((h) => (Number.isFinite(h) ? (small ? 6 : 9) + (small ? 6 : 10) * h / deepest : 9));
  const above = columnAt.some((column) => column && num(column.Ha) > 0) ? (small ? 10 : 14) : 0;
  // the own weight of each element, the analysis's once it answered, else 24.5 kN/m³ (0.150 kcf)
  const own = list.map((element, e) => {
    const known = (lastResult ?? lastAnalysis)?.elements?.[e]?.self_weight;
    if (Number.isFinite(known)) return known;
    const area = num(element.b) * num(element.h);
    return state.units === "us" ? area / 144 * 0.150 : area / 1e4 * 24.5;
  });
  const keyIndex = Object.fromEntries(list.map((element, index) => [element.key, index]));
  const bands = list.map((_, e) => [
    ...(own[e] > 0 ? [{ case: "PP", value: own[e], a: 0, b: lengths[e] }] : []),
    ...["D", "SDL", "L"].flatMap((kind) => state.loads
      .filter((load) => load.type === "uniform" && load.case === kind && keyIndex[load.on] === e && Number.isFinite(num(load.value)))
      .map((load) => ({
        case: kind, value: num(load.value),
        a: String(load.a ?? "").trim() === "" ? 0 : Math.max(0, num(load.a)),
        b: String(load.b ?? "").trim() === "" ? lengths[e] : Math.min(lengths[e], num(load.b)),
      }))),
  ]);
  const heaviest = Math.max(...bands.flat().map((band) => Math.abs(band.value)), 1e-9);
  const height = (band) => 6 + (small ? 12 : 18) * Math.abs(band.value) / heaviest;
  const gap = small ? 11 : 13;
  const stack = Math.max(0, ...bands.map((items) => items.reduce((sum, band) => sum + height(band) + gap, 0)));
  const loadTop = 14;
  const beamTop = loadTop + Math.max(small ? 30 : 40, stack + 6) + above;
  const colHeight = small ? 20 : 30;
  const beamBottom = beamTop + Math.max(...thick);
  // loads, stacked over each element, above any column that rises from the beam
  list.forEach((element, e) => {
    let base = beamTop - above - 3;
    for (const band of bands[e]) {
      if (!(band.b > band.a)) continue;
      const h = height(band);
      const [xa, xb] = [X(nodes[e] + band.a), X(nodes[e] + band.b)];
      parts.push(`<rect class="el-band el-band--${band.case}" x="${px(xa)}" y="${px(base - h)}" width="${px(xb - xa)}" height="${px(h)}"/>`);
      // arrows down, as a distributed load is drawn
      const count = Math.max(2, Math.round((xb - xa) / (small ? 16 : 22)));
      for (let k = 0; k <= count; k += 1) {
        const ax = xa + 3 + (xb - xa - 6) * k / count;
        parts.push(`<path class="el-arrow el-arrow--${band.case}" d="M${px(ax)} ${px(base - h + 2)}V${px(base - 1)}M${px(ax - 2.2)} ${px(base - 4)}L${px(ax)} ${px(base - 1)}L${px(ax + 2.2)} ${px(base - 4)}"/>`);
      }
      if (!small || xb - xa > 56) text((xa + xb) / 2, base - h - 3, `${fmt(band.value.toFixed(2))} ${LINE[state.units]}`, "el-t el-t--v");
      base -= h + gap;
    }
  });
  for (const load of state.loads.filter((item) => item.type === "point" && Number.isFinite(num(item.value)))) {
    const e = keyIndex[load.on];
    if (e === undefined) continue;
    const x = X(nodes[e] + Math.min(Math.max(num(load.a) || 0, 0), lengths[e]));
    parts.push(`<path class="el-load el-load--${load.case}" d="M${px(x)} ${loadTop + 8}V${beamTop - above - 2}M${px(x - 3)} ${beamTop - above - 7}L${px(x)} ${beamTop - above - 2}L${px(x + 3)} ${beamTop - above - 7}"/>`);
    text(x, loadTop + 4, `${fmt(num(load.value))} ${UNIT_WORDS[state.units].force}`, "el-t el-t--v");
  }
  // beam and columns, one grey body: outlined, then filled again over the joints
  const outer = (j) => (columnAt[j] ? (num(columnAt[j].a) || 0) * toSpan / 2 : 0);
  const shapes = list.map((element, e) => {
    const x0 = e === 0 ? nodes[0] - outer(0) : nodes[e];
    const x1 = e === list.length - 1 ? nodes[e + 1] + outer(nodes.length - 1) : nodes[e + 1];
    return [X(x0), beamTop, X(x1) - X(x0), thick[e]];
  });
  let name = 0;
  const names = [];
  nodes.forEach((x, j) => {
    const column = columnAt[j];
    if (!column) return;
    const half = (num(column.a) || 0) * toSpan / 2;
    const [xa, xb] = [X(x - half), X(x + half)];
    shapes.push([xa, beamTop + 1, Math.max(xb - xa, 2), beamBottom - beamTop + colHeight - 1]);
    if (num(column.Ha) > 0) shapes.push([xa, beamTop - above, Math.max(xb - xa, 2), above + 1]);
    names.push([X(x), String.fromCharCode(65 + name++), column.pinned]);
  });
  for (const [x, y, w, h] of shapes) parts.push(`<rect class="el-body" x="${px(x)}" y="${px(y)}" width="${px(w)}" height="${px(h)}"/>`);
  for (const [x, y, w, h] of shapes) parts.push(`<rect class="el-body-fill" x="${px(x + 0.7)}" y="${px(y + 0.7)}" width="${px(Math.max(w - 1.4, 0))}" height="${px(Math.max(h - 1.4, 0))}"/>`);
  for (const [x, label, pinned] of names) {
    if (pinned) parts.push(`<circle class="el-sup" cx="${px(x)}" cy="${px(beamBottom + 4)}" r="3"/>`);
    text(x, beamBottom + colHeight + 13, label);
  }
  const dimY = beamBottom + colHeight + (small ? 30 : 34);
  parts.push(`<path class="dw-dl" d="M${px(X(0) - 4)} ${dimY}H${px(X(nodes.at(-1)) + 4)}"/>`);
  nodes.forEach((x) => parts.push(`<path class="dw-dl" d="M${px(X(x) - 3)} ${dimY + 3}L${px(X(x) + 3)} ${dimY - 3}"/>`));
  list.forEach((element, e) => text(X(nodes[e] + lengths[e] / 2), dimY - 4, `${fmt(lengths[e])} ${SPAN[state.units]}`, "el-t el-t--v"));
  // the legend: each case drawn, its colour and its name (the units go with each value)
  const drawn = ["PP", "D", "SDL", "L"].filter((kind) => bands.flat().some((band) => band.case === kind)
    || state.loads.some((load) => load.case === kind && load.type === "point"));
  let lx = f.left;
  const ly = dimY + 22;
  for (const kind of drawn) {
    const label = kind === "PP" ? t.own_weight : caseName(kind);
    parts.push(`<rect class="el-band el-band--${kind}" x="${px(lx)}" y="${ly - 9}" width="14" height="10"/>`);
    text(lx + 19, ly, label, "el-t", "start");
    lx += 36 + label.length * (small ? 5.6 : 6.6);
  }
  const H = Math.ceil(ly + 8);
  $("drawing").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${escapeHtml(t.fig_elev)}">${parts.join("")}</svg>`;
}

// ------------------------------------------------------------ the diagrams
// One figure each, sharing the beam's horizontal scale, switched on and off by the chips.
function renderChips() {
  $("chips").innerHTML = CHARTS.map((key) => `<button type="button" data-chart="${key}" aria-pressed="${shown.has(key)}">${escapeHtml(t[`chart_${key}`])}</button>`).join("");
}
$("chips").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-chart]");
  if (!button) return;
  const key = button.dataset.chart;
  shown.has(key) ? shown.delete(key) : shown.add(key);
  try { localStorage.setItem(`mento-${MODULE}-charts`, JSON.stringify([...shown])); } catch { /* storage blocked */ }
  renderChips();
  renderCharts();
});

function renderCharts() {
  const result = lastResult?.designs ? lastResult : lastAnalysis;
  const f = frame();
  if (!result?.diagrams || !f || result.elements.length !== f.list.length) { $("charts").innerHTML = ""; return; }
  const U = UNIT_WORDS[state.units];
  const units = { M: U.moment, V: U.force, v: SMALL[state.units], As: t.as_axis.replace("{u}", `${U.len}²`), Av: `${U.len}²/${state.units === "us" ? "ft" : "m"}` };
  const figures = [];
  for (const key of CHARTS) {
    if (!shown.has(key)) continue;
    if ((key === "As" || key === "Av" || key === "rebar") && !lastResult?.designs) continue;
    const svg = { M: momentChart, V: shearChart, v: deflectionChart, As: steelChart, Av: stirrupChart, rebar: rebarChart }[key](result, f);
    const unit = units[key] ? ` <span class="unit">· ${escapeHtml(units[key])}</span>` : "";
    figures.push(`<figure><figcaption>${escapeHtml(t[`chart_${key}`])}${unit}</figcaption>${svg}</figure>`);
  }
  $("charts").innerHTML = figures.join("");
}

// A band of one quantity along the beam: an axis, the faces dashed, and Y mapping a value to px.
function band(parts, f, height, low, high, down) {
  const above = Math.max(0, ...(down ? low.map((v) => -v) : high));
  const below = Math.max(0, ...(down ? high : low.map((v) => -v)));
  const scale = height / Math.max(above + below, 1e-9);
  const top = 16;
  const axis = top + above * scale;
  const Y = (v) => axis + (down ? v : -v) * scale;
  parts.push(`<path class="el-axis" d="M${px(f.X(0))} ${px(axis)}H${px(f.X(f.nodes.at(-1)))}"/>`);
  return { Y, axis, H: Math.ceil(top + height + 18) };
}
const svg = (f, H, parts, label) => `<svg viewBox="0 0 ${f.W} ${H}" width="${f.W}" height="${H}" role="img" aria-label="${escapeHtml(label)}">${parts.join("")}</svg>`;
const envelope = (f, Y, x, low, high, cls) => {
  const forward = x.map((xi, i) => `${px(f.X(xi))} ${px(Y(high[i]))}`);
  const back = x.map((xi, i) => `${px(f.X(xi))} ${px(Y(low[i]))}`).reverse();
  return `<path class="${cls}" d="M${forward.join("L")}L${back.join("L")}Z"/>`;
};
// the value of a diagram exactly at a face: the glue puts a station there, from each side
const atFace = (d, x, after) => d.x.findIndex((xi, i) => Math.abs(xi - x) < 1e-6 && d.after[i] === after);

function faceLines(parts, f, result, top, bottom) {
  for (const element of result.elements) {
    for (const x of element.faces) parts.push(`<path class="el-face" d="M${px(f.X(x))} ${px(top)}V${px(bottom)}"/>`);
  }
}

// The envelope shifted by d to each side, filled: what the bars are designed and cut for. The
// envelope as the analysis gives it is the line inside, and the elastic one, dashed, when the
// moments are redistributed.
function momentChart(result, f) {
  const d = result.diagrams;
  const high = d.M_shift_max ?? d.M_max;
  const low = d.M_shift_min ?? d.M_min;
  const parts = [];
  const text = svgText(parts, f);
  const { Y, H } = band(parts, f, f.small ? 90 : 130, low, high, true);
  faceLines(parts, f, result, 10, H - 6);
  parts.push(envelope(f, Y, d.x, low, high, "el-env"));
  const line = (values, cls) => `<path class="${cls}" d="M${d.x.map((xi, i) => `${px(f.X(xi))} ${px(Y(values[i]))}`).join("L")}"/>`;
  parts.push(line(d.M_max, "el-unshifted"), line(d.M_min, "el-unshifted"));
  if (num(state.redis) > 0) parts.push(line(d.M_elastic_min, "el-elastic"));
  // each span's largest sagging moment, and over each support its hogging peak, rounded inside
  // the column, the moment its top steel is designed for
  result.elements.forEach((element) => {
    const [start, end] = element.faces;
    let best = -Infinity;
    let at = 0;
    d.x.forEach((xi, i) => { if (xi >= start && xi <= end && d.M_max[i] > best) { best = d.M_max[i]; at = xi; } });
    if (best > 0.05) text(f.X(at), Y(best) + 13, fmt(best.toFixed(1)), "el-t el-t--v");
  });
  const els = result.elements;
  const zones = els.slice(1).map((element, e) => [els[e].faces[1], element.faces[0]]);
  if (els[0].kind === "span") zones.unshift([els[0].x0, els[0].faces[0]]);
  if (els.at(-1).kind === "span") zones.push([els.at(-1).faces[1], els.at(-1).x1]);
  for (const [a, b] of zones) {
    let peak = Infinity;
    let at = a;
    d.x.forEach((xi, i) => { if (xi >= a - 1e-9 && xi <= b + 1e-9 && d.M_min[i] < peak) { peak = d.M_min[i]; at = xi; } });
    if (peak < -0.05) text(f.X(at), Y(peak) - 5, fmt(peak.toFixed(1)), "el-t el-t--v");
  }
  return svg(f, H, parts, t.chart_M);
}

function shearChart(result, f) {
  const d = result.diagrams;
  const parts = [];
  const text = svgText(parts, f);
  const { Y, H } = band(parts, f, f.small ? 70 : 100, d.V_min, d.V_max, false);
  faceLines(parts, f, result, 10, H - 6);
  parts.push(envelope(f, Y, d.x, d.V_min, d.V_max, "el-env el-env--V"));
  // the shear at each face, where the diagram steps
  for (const element of result.elements) {
    const [start, end] = element.faces;
    const i = atFace(d, start, true);
    const k = atFace(d, end, false);
    if (i >= 0 && Math.abs(d.V_max[i]) > 0.05) text(f.X(start) + 2, Y(d.V_max[i]) - 4, fmt(d.V_max[i].toFixed(1)), "el-t el-t--v", "start");
    if (k >= 0 && Math.abs(d.V_min[k]) > 0.05) text(f.X(end) - 2, Y(d.V_min[k]) + 13, fmt(d.V_min[k].toFixed(1)), "el-t el-t--v", "end");
  }
  return svg(f, H, parts, t.chart_V);
}

function deflectionChart(result, f) {
  const d = result.diagrams;
  const factor = num(state.defl) || 1;
  const v = d.v_min.map((value) => value * factor);
  const parts = [];
  const text = svgText(parts, f);
  const { Y, H } = band(parts, f, f.small ? 40 : 56, v, d.v_max.map((value) => value * factor), false);
  faceLines(parts, f, result, 10, H - 6);
  parts.push(`<path class="el-defl" d="M${d.x.map((xi, i) => `${px(f.X(xi))} ${px(Y(v[i]))}`).join("L")}"/>`);
  for (const row of result.deflections) {
    const [start, end] = result.elements[row.element].faces;
    if (row.deflection > 0) text(f.X((start + end) / 2), H - 4, `${fmt(row.deflection)} ${SMALL[state.units]} · L/${row.ratio ?? "∞"}`, "el-t el-t--v");
  }
  return svg(f, H, parts, t.chart_v);
}

// The longitudinal steel: the top face above the axis, the bottom one under it; what the shifted
// moment asks for at each station, filled, against what is placed, base and additions, in green.
function steelChart(result, f) {
  const parts = [];
  const text = svgText(parts, f);
  const designs = result.designs;
  const placed = (design, face) => design.profile[`As_${face}_placed`] ?? design.profile.x.map(() => design.profile[`As_${face}`]);
  const top = designs.flatMap((design) => [...design.profile.As_top_req, ...placed(design, "top")]);
  const bottom = designs.flatMap((design) => [...design.profile.As_bot_req, ...placed(design, "bot")]);
  const { Y, H } = band(parts, f, f.small ? 90 : 120, bottom.map((value) => -value), top, false);
  faceLines(parts, f, result, 10, H - 6);
  designs.forEach((design, e) => {
    const p = design.profile;
    const xs = p.x;
    const area = (values, sign) => `<path class="el-req" d="M${px(f.X(xs[0]))} ${px(Y(0))}L${xs.map((xi, i) => `${px(f.X(xi))} ${px(Y(sign * values[i]))}`).join("L")}L${px(f.X(xs.at(-1)))} ${px(Y(0))}Z"/>`;
    parts.push(area(p.As_top_req, 1), area(p.As_bot_req, -1));
    // the placed steel steps where an addition starts or stops: from the bars' own extents
    const steps = (face, sign) => {
      const bars = design.bars;
      const base = bars?.[`${face}_base`]?.As ?? placed(design, face)[0];
      const [start, end] = result.elements[e].faces;
      const cuts = [...new Set([start, end, ...(bars?.[`${face}_add`] ?? []).flatMap((add) => [add.x0, add.x1])])].sort((a, b) => a - b);
      const value = (x) => base + (bars?.[`${face}_add`] ?? []).filter((add) => add.x0 - 1e-6 <= x && x <= add.x1 + 1e-6).reduce((sum, add) => sum + add.As, 0);
      const points = [];
      const levels = [];  // each stretch at one area: where it runs and how much
      for (let i = 0; i < cuts.length - 1; i += 1) {
        const area = value((cuts[i] + cuts[i + 1]) / 2);
        const level = Y(sign * area);
        points.push(`${px(f.X(cuts[i]))} ${px(level)}`, `${px(f.X(cuts[i + 1]))} ${px(level)}`);
        const last = levels.at(-1);
        if (last && Math.abs(last.area - area) < 1e-6) last.x1 = cuts[i + 1];
        else levels.push({ x0: cuts[i], x1: cuts[i + 1], area });
      }
      for (const level of levels) {
        if (f.X(level.x1) - f.X(level.x0) < (f.small ? 22 : 28)) continue;
        text(f.X((level.x0 + level.x1) / 2), Y(sign * level.area) + (sign > 0 ? -4 : 12), fmt((level.area + 1e-9).toFixed(2)), "el-t el-t--placed");
      }
      return `<path class="el-placed" d="M${points.join("L")}"/>`;
    };
    parts.push(steps("top", 1), steps("bot", -1));
  });
  return svg(f, H, parts, t.chart_As);
}

// The stirrups: the Av the shear asks for at each station against what each zone carries.
function stirrupChart(result, f) {
  const parts = [];
  const text = svgText(parts, f);
  const designs = result.designs;
  const values = designs.flatMap((design) => [...design.profile.Av_req, ...design.zones.map((zone) => zone.Av ?? 0)]);
  const { Y, H } = band(parts, f, f.small ? 60 : 80, [0], values, false);
  faceLines(parts, f, result, 10, H - 6);
  designs.forEach((design) => {
    const p = design.profile;
    parts.push(`<path class="el-req" d="M${px(f.X(p.x[0]))} ${px(Y(0))}L${p.x.map((xi, i) => `${px(f.X(xi))} ${px(Y(p.Av_req[i]))}`).join("L")}L${px(f.X(p.x.at(-1)))} ${px(Y(0))}Z"/>`);
    for (const zone of design.zones) {
      if (!zone.Av) continue;
      parts.push(`<path class="el-placed" d="M${px(f.X(zone.x0))} ${px(Y(zone.Av))}H${px(f.X(zone.x1))}"/>`);
    }
  });
  return svg(f, H, parts, t.chart_Av);
}

// The bars, schematic, as they would be placed: the top base along the whole beam over the
// columns, the bottom base span by span, each addition where the shifted moment needs it plus
// l_bII,min, the top ones continuous over their column. Under the beam, the stirrup zones with
// their spacing and their lengths; the bars named above and below.
function rebarChart(result, f) {
  const parts = [];
  const text = svgText(parts, f);
  const { nodes, columnAt, X, toSpan, small } = f;
  const top = small ? 30 : 34;
  const depth = small ? 22 : 30;
  const colHeight = 12;
  const halves = nodes.map((x, j) => (columnAt[j] ? (num(columnAt[j].a) || 0) * toSpan / 2 : 0));
  const shapes = result.elements.map((element, e) => {
    const x0 = e === 0 ? nodes[0] - halves[0] : nodes[e];
    const x1 = e === result.elements.length - 1 ? nodes[e + 1] + halves.at(-1) : nodes[e + 1];
    return [X(x0), top, X(x1) - X(x0), depth];
  });
  nodes.forEach((x, j) => {
    if (!columnAt[j]) return;
    shapes.push([X(x - halves[j]), top + 1, Math.max(X(x + halves[j]) - X(x - halves[j]), 2), depth + colHeight - 1]);
  });
  for (const [x, y, w, h] of shapes) parts.push(`<rect class="el-body" x="${px(x)}" y="${px(y)}" width="${px(w)}" height="${px(h)}"/>`);
  for (const [x, y, w, h] of shapes) parts.push(`<rect class="el-body-fill" x="${px(x + 0.7)}" y="${px(y + 0.7)}" width="${px(Math.max(w - 1.4, 0))}" height="${px(Math.max(h - 1.4, 0))}"/>`);
  const first = nodes[0] - halves[0];
  const last = nodes.at(-1) + halves.at(-1);
  const yTop = top + 4;
  const yTopAdd = top + 8;
  const yBot = top + depth - 4;
  const yBotAdd = top + depth - 8;
  const line = (x0, x1, y) => parts.push(`<path class="el-bar" d="M${px(X(x0))} ${y}H${px(X(x1))}"/>`);
  // the top base, end to end over the columns; named over each span
  line(first + 0.02, last - 0.02, yTop);
  result.designs.forEach((design, e) => {
    const [start, end] = result.elements[e].faces;
    const bars = design.bars ?? {};
    text(X((start + end) / 2), top - 5, bars.top_base?.bars ?? design.rebar.top, "el-t--bar");
    // the bottom base, face to face into each column
    line(Math.max(start - halves[e], first) + 0.02, Math.min(end + halves[e + 1], last) - 0.02, yBot);
    const labelBot = [bars.bot_base?.bars ?? design.rebar.bot, ...(bars.bot_add ?? []).map((add) => `+ ${add.bars}`)].join(" ");
    for (const add of bars.bot_add ?? []) line(add.x0, add.x1, yBotAdd);
    text(X((start + end) / 2), top + depth + colHeight + 12, labelBot, "el-t--bar");
  });
  // the top additions, joined across the column they stand over
  nodes.forEach((x, j) => {
    const left = result.designs[j - 1]?.bars?.top_add?.find((add) => add.at === "right" || add.at === "both");
    const right = result.designs[j]?.bars?.top_add?.find((add) => add.at === "left" || add.at === "both");
    if (!left && !right) return;
    const x0 = left ? left.x0 : x - halves[j];
    const x1 = right ? right.x1 : x + halves[j];
    line(x0, x1, yTopAdd);
    const name = [left, right].filter(Boolean).sort((a, b) => b.As - a.As)[0].bars;
    text(X((x0 + x1) / 2), top - 17, `+ ${name}`, "el-t--bar");
  });
  // the stirrup zones, equal neighbours as one: ticks at their ends in the beam, their spacing
  // and their lengths on a chain of dimensions under it
  const dimY = top + depth + colHeight + (small ? 34 : 38);
  result.designs.forEach((design) => {
    const zones = [];
    for (const zone of design.zones) {
      const previous = zones.at(-1);
      if (previous && previous.bars === zone.bars) previous.x1 = zone.x1; else zones.push({ ...zone });
    }
    for (const zone of zones) {
      parts.push(`<path class="el-stirrup" d="M${px(X(zone.x0))} ${top + 2}V${top + depth - 2}M${px(X(zone.x1))} ${top + 2}V${top + depth - 2}"/>`);
      const mid = X((zone.x0 + zone.x1) / 2);
      const wide = X(zone.x1) - X(zone.x0);
      parts.push(`<path class="dw-dl" d="M${px(X(zone.x0))} ${dimY}H${px(X(zone.x1))}M${px(X(zone.x0) - 3)} ${dimY + 3}l6 -6M${px(X(zone.x1) - 3)} ${dimY + 3}l6 -6"/>`);
      if (!small || wide > 34) text(mid, dimY - 3, fmt((zone.x1 - zone.x0).toFixed(2)), "el-t el-t--v");
      if (!small || wide > 70) text(mid, dimY + 13, zone.bars ?? "", "el-t--st");
    }
  });
  const H = dimY + 20;
  return svg(f, H, parts, t.chart_rebar);
}

// ------------------------------------------------------------ results
const LEVELS = { bad: ["i-cross", "notice_bad"], warn: ["i-warn", "notice"], info: ["i-info", "notice_info"] };
const level = (item) => (item.severity in LEVELS ? item.severity : "warn");
function notice(kind, html) {
  return `<div class="notice notice--${kind}" role="${kind === "bad" ? "alert" : "status"}"><svg width="16" height="16"><use href="#${LEVELS[kind][0]}"/></svg><span>${html}</span></div>`;
}

function revealOutput() {
  $("loading").hidden = true;
  $("output").hidden = false;
}

// What the analysis alone already says: the reactions, the diagrams that need no design.
function renderAnalysis(result) {
  revealOutput();
  if (!lastResult) $("verdict").hidden = true;
  renderReactions(result);
  renderCharts();
}

function render(result) {
  if (!result.ok) return renderError(result);
  revealOutput();
  $("verdict").hidden = false;
  const before = lastResult;
  lastResult = result;
  lastAnalysis = result;
  const failed = result.complies === false;
  const status = failed ? "bad" : dcrState(result.dcr);
  const word = status === "bad" ? t.fails : status === "warn" ? t.passes_limit : t.passes;
  $("verdict").dataset.state = status;
  $("verdict-word").textContent = word;
  tween($("verdict-dcr"), before?.dcr, result.dcr, two);
  $("verdict").querySelector("use").setAttribute("href", status === "bad" ? "#i-cross" : "#i-check");
  $("strip").dataset.state = status;
  $("strip").innerHTML = `<span class="mark"><svg width="14" height="14"><use href="#${status === "bad" ? "i-cross" : "i-check"}"/></svg></span>`
    + `<span class="word">${escapeHtml(word)}</span><span class="readout">${two(result.dcr)}</span>`;

  // what mento says of each span, said once with the spans it concerns; then each support whose
  // redistribution goes past what its sections allow
  const names = result.elements.map((element) => element.name);
  const grouped = new Map();
  result.designs.forEach((design, e) => {
    for (const item of design.notices) {
      const key = `${item.code}|${item.message}`;
      if (!grouped.has(key)) grouped.set(key, { item, where: [] });
      grouped.get(key).where.push(names[e]);
    }
  });
  const items = [...grouped.values()].map(({ item, where }) => ({ kind: level(item), html: `<b>${escapeHtml(t[LEVELS[level(item)][1]])}</b> ${escapeHtml(where.join(", "))}: ${escapeHtml(item.message ?? "")}` }));
  const supports = [];
  for (const support of result.supports) {
    const check = support.redistribution;
    if (!check || check.ok !== false) continue;
    const reason = { ductility: t.redis_ductility, spans: t.redis_spans, unknown: t.redis_unknown }[check.reason] ?? "";
    const over = check.max === null ? "" : t.redis_over.replace("{name}", support.name).replace("{req}", fmt(check.requested)).replace("{max}", fmt(check.max));
    supports.push({ kind: "bad", html: `<b>${escapeHtml(t.notice_bad)}</b> ${escapeHtml([over, reason].filter(Boolean).join(" "))}` });
  }
  items.unshift(...supports);
  const order = Object.keys(LEVELS);
  items.sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));
  $("notices").innerHTML = items.map((item) => notice(item.kind, item.html)).join("");
  const notes = items.filter((item) => item.kind === "info").length;
  const flagged = items.length - notes;
  const counted = (count, one, many) => (count === 1 ? one : many.replace("{n}", count));
  $("warn-count").textContent = [flagged && counted(flagged, t.warn_one, t.warn_many), notes && counted(notes, t.note_one, t.note_many)].filter(Boolean).join(" · ") || t.nowarn;

  renderLedger(result);
  renderReactions(result);
  renderCharts();
  renderPython();
  clearStale();
}

// One card per span: its bars, its stirrups by zone, its worst DCR, what it was designed for.
function renderLedger(result) {
  const U = UNIT_WORDS[state.units];
  $("ledger").innerHTML = result.designs.map((design, e) => {
    const worst = Math.max(design.flexure.bottom.DCR, design.flexure.top.DCR, design.shear.DCR);
    const tone = design.complies ? dcrState(worst) : "bad";
    const zones = design.zones.length === 3 && design.zones[1].bars !== design.zones[0].bars
      ? `${design.zones[0].bars} · ${design.zones[1].bars}` : design.rebar.st;
    const row = (label, value, dcr) => `<div class="r"><span class="k">${escapeHtml(label)}</span><span class="v">${escapeHtml(value ?? "—")}`
      + `${dcr === undefined ? "" : ` <span class="dcr ${dcrState(dcr)}">${two(dcr)}</span>`}</span></div>`;
    const demands = Object.values(design.demands).map((demand) => `${demand.label} ${fmt(demand.M)} ${U.moment}${demand.V ? ` · V ${fmt(demand.V)}` : ""}`).join("<br>");
    // the top steel at each support and the bottom one in the span, each with its own DCR
    const at = (key) => design.demands[key]?.DCR;
    // what is placed there: the face's base and the additions that reach it
    const laid = (face, where) => {
      const bars = design.bars;
      if (!bars?.[`${face}_base`]) return design.rebar[face];
      const adds = (bars[`${face}_add`] ?? []).filter((add) => !where || add.at === where || add.at === "both");
      return [bars[`${face}_base`].bars, ...adds.map((add) => add.bars)].join(" + ");
    };
    const faces = [
      ["M-l", t.top_left, laid("top", "left")], ["M-o", t.top_support, laid("top")],
      ["M+", t.bot_mid, laid("bot")], ["M-r", t.top_right, laid("top", "right")],
    ].filter(([key]) => key in design.demands);
    return `<div class="cb-span"><div class="hd"><span>${escapeHtml(result.elements[e].name)}</span><span class="dcr ${tone}">${two(worst)}</span></div>`
      + faces.map(([key, label, bars]) => row(label, bars, at(key) ?? (key === "M+" ? design.flexure.bottom.DCR : design.flexure.top.DCR))).join("")
      + row(t.st, zones, design.shear.DCR) + `<div class="d">${demands}</div></div>`;
  }).join("");
}

function renderReactions(result) {
  const force = UNIT_WORDS[state.units].force;
  $("reactions").innerHTML = `<table class="df"><thead><tr><th>${escapeHtml(t.t_support)}</th><th>${escapeHtml(t.t_service)}<br><span class="unit">${force}</span></th>`
    + `<th>${escapeHtml(t.t_umax)}<br><span class="unit">${force}</span></th><th>${escapeHtml(t.t_umin)}<br><span class="unit">${force}</span></th></tr></thead><tbody>`
    + (result.supports || []).map((support) => `<tr><td>${escapeHtml(support.name)}</td><td>${fmt(support.R_service)}</td><td>${fmt(support.R_max)}</td><td>${fmt(support.R_min)}</td></tr>`).join("")
    + "</tbody></table>";
}

function renderError(result) {
  if (result.kind === "input") { showInvalid(); return; }
  revealOutput();
  $("verdict").hidden = true;
  $("notices").innerHTML = notice("bad", `<b>${escapeHtml(t.cannot)}</b> <span class="mono">${escapeHtml(result.message)}</span>`);
}

// ------------------------------------------------------------ the Python snippet
// The bridge to the package: mento designs each span's section with the forces the page found.
// The analysis is the page's, not mento's: the snippet says so and carries its numbers.
function python() {
  const P = pyUnits(state.units);
  const concrete = CONCRETE_CLASS[state.code];
  const delta = state.code === "EN 1992-2004" && num(state.redis) > 15 ? `, delta=${fmt(1 - num(state.redis) / 100)}` : "";
  const lines = [
    `from mento import ${concrete}, SteelBar, RectangularBeam, Node, Forces`,
    P.imports,
    "",
    ...t.py_intro.match(/.{1,92}(\s|$)/g).map((line) => `# ${line.trim()}`),
    `concrete = ${concrete}(name="${state.code}", f_c=${P.fc(state.fc)}${delta})`,
    `steel = SteelBar(name="fy ${state.fy}", f_y=${P.fy(state.fy)})`,
  ];
  const result = lastResult?.ok ? lastResult : null;
  elements().forEach((element, e) => {
    const variable = `beam_${e + 1}`;
    lines.push("", `# ${element.name}`);
    lines.push(`${variable} = RectangularBeam(label="${element.name}", concrete=concrete, steel_bar=steel,`);
    lines.push(`    width=${P.len(num(element.b))}, height=${P.len(num(element.h))}, c_c=${P.cov(num(state.cover))})`);
    const demands = result?.designs?.[e]?.demands;
    if (demands) {
      const rows = Object.values(demands).filter((demand) => Math.abs(demand.M) > 0.005 || demand.V > 0.005);
      lines.push("forces = [");
      for (const demand of rows) lines.push(`    Forces(label="${demand.label}", M_y=${P.moment(demand.M)}, V_z=${P.force(demand.V)}),`);
      lines.push("]");
    } else {
      lines.push("forces = []  # …");
    }
    lines.push(`node = Node(section=${variable}, forces=forces)`, "node.design()", `print(${variable}.flexure_design.complies, ${variable}.shear_design.DCR)`);
  });
  return lines.join("\n");
}
const renderPython = () => { $("python").innerHTML = highlightPython(python()); };

// ------------------------------------------------------------ inputs and wiring
function renderInputs() {
  renderMaterials();
  renderUnits();
  $("cover").value = state.cover;
  $("redis").value = state.redis;
  $("defl").value = state.defl;
  $("head").value = state.head;
  $("pattern").checked = Boolean(state.pattern);
  $("label").value = state.label;
  renderSpans();
  renderLoads();
  renderChips();
}

function linkHome() {
  for (const link of document.querySelectorAll('a[href^="../"]')) {
    const [path, anchor] = link.getAttribute("href").split("#");
    if (path.split("?")[0] !== "../") continue;
    link.setAttribute("href", `../?l=${lang}${anchor ? `#${anchor}` : ""}`);
  }
}

function applyLanguage(next) {
  lang = next;
  rememberLang(lang);
  linkHome();
  t = applyStrings(strings, lang, document, state.units);
  renderInputs();
  if (lastResult) render(lastResult); else renderDrawing();
  writeHash();
  schedule({ now: true });
}

for (const id of ["cover", "redis", "defl", "head"]) numberField($(id), () => { state[id] = $(id).value; schedule(); });
$("label").addEventListener("input", () => { state.label = $("label").value; renderPython(); writeHash(); });
$("pattern").addEventListener("change", () => { state.pattern = $("pattern").checked; renderCombos(); schedule({ now: true }); });
for (const id of ["base", "above"]) $(id).addEventListener("change", () => { state[id] = $(id).value; schedule({ now: true }); });
$("code").addEventListener("change", () => {
  state.code = $("code").value;
  const materials = materialsFor(state.units)[state.code];
  const [fc, fy] = (state.units === "us" ? DEFAULT_MATERIALS_US : DEFAULT_MATERIALS)[state.code];
  if (!materials.concrete.some(([value]) => value === state.fc)) state.fc = fc;
  if (!materials.steel.some(([value]) => value === state.fy)) state.fy = fy;
  renderMaterials();
  renderLoads();
  schedule({ now: true });
});
$("fc").addEventListener("change", () => { state.fc = Number($("fc").value); schedule({ now: true }); });
$("fy").addEventListener("change", () => { state.fy = Number($("fy").value); schedule({ now: true }); });
radiogroup(document.querySelector(".top .seg"), (button) => applyLanguage(button.dataset.lang));
radiogroup($("units"), (button) => setUnits(button.dataset.units));

// What a paid tier could deliver, asked rather than offered: the answer is counted, never anything
// the visitor typed.
const SURVEY_KEY = `mento-${MODULE}-survey`;
function renderSurvey() {
  let sent = false;
  try { sent = localStorage.getItem(SURVEY_KEY) === "1"; } catch { /* storage blocked */ }
  $("survey-send").disabled = sent;
  $("survey-send").textContent = sent ? t.survey_thanks : t.survey_send;
}
$("survey-send").addEventListener("click", () => {
  const picked = $("survey").querySelector('input[type="radio"]:checked');
  if (!picked) return toast(t.survey_pick);
  track("survey_deliverables", { ...context(), choice: picked.value });
  try { localStorage.setItem(SURVEY_KEY, "1"); } catch { /* storage blocked */ }
  toast(t.survey_thanks);
  renderSurvey();
});
$("share").addEventListener("click", () => { writeHash(); copy(location.href, t.link_copied); track("share_link", context()); });
$("copy-python").addEventListener("click", (event) => {
  event.preventDefault(); event.stopPropagation();
  copy($("python").textContent, t.code_copied);
  track("copy_python", context());
});
for (const details of document.querySelectorAll(".disc")) {
  foldable(details);
  const key = `mento-${MODULE}-${details.id}`;
  try { details.open = localStorage.getItem(key) === "1" || (localStorage.getItem(key) === null && details.open); } catch { /* storage blocked */ }
  details.addEventListener("toggle", () => { try { localStorage.setItem(key, details.open ? "1" : "0"); } catch { /* storage blocked */ } });
}
window.matchMedia("(max-width: 720px)").addEventListener("change", () => { renderDrawing(); renderCharts(); });

linkHome();
renderInputs();
renderSurvey();
validate();
openingInputs = inputsOf(payload());
renderPython();
renderDrawing();
showStep("python");
if (!ready) showSaved();
