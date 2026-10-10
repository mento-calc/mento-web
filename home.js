// Home: its movement, language, the test count CI publishes, and a head start on the calculator.
import { LANGS, applyStrings, loadStrings, preferredLang, rememberLang } from "./shared/i18n.js";
import { calm, radiogroup, tween } from "./shared/ui.js";

// ------------------------------------------------------------ the hero in US units
// The markup is the metric example. The page in English shows beam/'s US example instead (the JSON
// under the hero, which py/test_site.py checks against mento): the home has no units switch, its
// units are its language's (the calculators keep their own). Drawn in the same frame: the section's
// height takes the 300 px the metric one does, and everything else scales with it. First thing, like
// the movement below, so the metric hero never shows in English and then changes.
const hero = document.getElementById("hero");
const card = document.querySelector(".ccard--featured .fig svg");
const HERO_SI = { hero: hero.innerHTML, top: hero.dataset.top, st: hero.dataset.st, card: card.innerHTML };
const HERO_US = JSON.parse(document.getElementById("hero-us").textContent);
const unitsOf = (language) => (language === "en" ? "us" : "si");
// A shared link names its language (?l=en), over the visitor's own: the address keeps the one on
// screen, so the link copied from it opens as it was seen. Not a #: the home's anchors use it.
const linkedLang = new URLSearchParams(location.search).get("l");
const firstLang = LANGS.includes(linkedLang) ? linkedLang : preferredLang();
let units = unitsOf(firstLang);

function beamDrawing(data, hooks) {
  const scale = 300 / data.height;
  const w = data.width * scale;
  const cx = (x) => (x * scale).toFixed(2);
  const cy = (y) => (300 - y * scale).toFixed(2);
  const r = (d) => Math.max(2.5, (d / 2) * scale).toFixed(2);
  const hook = (name) => (hooks ? ` data-hero="${name}"` : "");
  const low = data.bars.filter(([, y]) => y < data.height / 2);
  const high = data.bars.filter(([, y]) => y >= data.height / 2);
  const { x0, x1, y0, y1 } = data.closed;  // the stirrup, by its centreline
  const label = (y, text, name, [from, extra] = [w - 16, ""]) => `<line x1="${from}" y1="${y}" x2="${w + 16}" y2="${y}"`
    + ` stroke="#5b6470" stroke-width=".8"/><text class="dw-label${extra}" x="${w + 20}" y="${Number(y) + 3.5}" font-size="14"${hook(name)}>${text}</text>`;
  // "per side" is the dictionary's: it comes with the rest of the page's words
  const skin = `<tspan${hook("skin")}>${data.skin}</tspan> <tspan data-i18n="per_side"></tspan>`;
  return `<rect class="dw-concrete" x="0" y="0" width="${w}" height="300"/>`
    + `<rect class="dw-stirrup" x="${cx(x0)}" y="${cy(y1)}" width="${cx(x1 - x0)}" height="${((y1 - y0) * scale).toFixed(2)}"`
    + ` rx="${(((data.stirrup.bend + data.stirrup.d) / 2) * scale).toFixed(1)}" style="stroke-width:${(data.stirrup.d * scale).toFixed(1)}"/>`
    + low.map(([x, y, d]) => `<circle class="dw-bar"${hooks ? " data-mid" : ""} cx="${cx(x)}" cy="${cy(y)}" r="${r(d)}"/>`).join("")
    + [...high, ...data.skin_bars].map(([x, y, d]) => `<circle class="dw-bar" cx="${cx(x)}" cy="${cy(y)}" r="${r(d)}"/>`).join("")
    + label(cy(high[0][1]), data.top, "top") + label(112, data.stirrups, "stirrups", [w - 11, " dw-label--stirrup"])
    + label(cy(data.skin_bars[0][1]), skin, null, [w - 13, ""])
    + label(cy(low[0][1]), data.options[0].bars, "bottom")
    + `<path class="dw-dl" d="M0 304V318M${w} 304V318M-4 314H${w + 4}M-3 317L3 311M${w - 3} 317L${w + 3} 311"/>`
    + `<text class="dw-dt" x="${w / 2}" y="330" text-anchor="middle"${hook("width")}>${data.width} in</text>`
    + '<path class="dw-dl" d="M-4 0H-18M-4 300H-18M-14 -4V304M-17 3L-11 -3M-17 303L-11 297"/>'
    + `<text class="dw-dt" x="-21" y="150" text-anchor="middle" transform="rotate(-90 -21 150)"${hook("height")}>${data.height} in</text>`;
}

// The hero and the beam's card in one system: the metric markup as it came, or the US example drawn over it.
function showUnits(next) {
  hero.innerHTML = HERO_SI.hero;
  Object.assign(hero.dataset, { top: HERO_SI.top, st: HERO_SI.st });
  delete hero.dataset.scale;
  card.innerHTML = HERO_SI.card;
  if (next !== "us") return;
  const data = HERO_US;
  Object.assign(hero.dataset, { top: data.data_top, st: data.data_st, scale: 300 / data.height });
  // room on the right for the labels (the stirrups' is the longest), the height's dimension line on the left
  const left = Math.max(32, Math.min(56, 310 - (data.width * 300) / data.height - 124));
  for (const [svg, hooks] of [[hero.querySelector(".viz-draw svg"), true], [card, false]]) {
    const group = svg.querySelector("g");
    group.setAttribute("transform", `translate(${left},10)`);
    group.innerHTML = beamDrawing(data, hooks);
  }
  // As many options as mento proposes in this system, which need not be as many as in the other:
  // the markup's first is the mould of them all.
  const mould = hero.querySelector(".opt");
  const more = hero.querySelector(".viz-prop .more");
  hero.querySelectorAll(".opt").forEach((option) => option.remove());
  data.options.forEach((values, index) => {
    const option = mould.cloneNode(true);
    Object.assign(option.dataset, { sig: values.sig, mid: values.low });
    option.querySelector("input").checked = index === 0;
    for (const [name, text, cell] of [[".val", values.bars, `opt${index + 1}`], [".area", values.area], [".dcr", values.dcr, `opt${index + 1}_dcr`]]) {
      option.querySelector(name).textContent = text;
      if (cell) option.querySelector(name).dataset.hero = cell;
    }
    more.before(option);
  });
  const bottom = Number(data.options[0].dcr);
  const [top, shear] = [Number(data.top_dcr), Number(data.shear_dcr)];
  const cells = { bottom_dcr: bottom, top_dcr: top, shear_dcr: shear, dcr: Math.max(bottom, top, shear), sum_flex: Math.max(bottom, top), sum_shear: shear };
  for (const [name, value] of Object.entries(cells)) hero.querySelector(`[data-hero="${name}"]`).textContent = value.toFixed(2);
  hero.querySelectorAll(".mini .meter i").forEach((meter, index) => { meter.style.width = `${Math.min([bottom, top, shear][index], 1) * 100}%`; });
}
if (units === "us") showUnits("us");

// ------------------------------------------------------------ movement (design system v2)
// First thing, before the dictionary loads, so nothing on screen shows and then hides again.
// Progressive, like the rest of it: the markup carries the final state, and the classes and values
// that animate towards it are set here, only when there is motion to show (not under reduced
// motion, not in a tab opened in the background). Without this script the page is whole and still.
const moving = !calm();

// The hero is the calculator's result assembling (the stylesheet times it); its DCRs count up.
// Each keeps its value aside while it counts, and the timer that starts it, for a pick to cancel.
const countUps = new Map();
if (moving) {
  for (const cell of document.querySelectorAll('.viz-verdict [data-hero$="dcr"]')) {
    const to = Number(cell.textContent);
    cell.dataset.value = to;
    cell.textContent = (0).toFixed(2);
    countUps.set(cell, setTimeout(() => tween(cell, 0, to, (value) => value.toFixed(2), 700), 1100));
  }
}

// ------------------------------------------------------------ the hero, live
// Picking another of mento's options redraws its bars and moves the bottom flexure DCR, as the
// calculator does; each option's numbers are mento's (the markup carries them, the tests check
// them). Its link opens the beam calculator with that option loaded, to check it or change it.
const heroCell = (name) => hero.querySelector(`[data-hero="${name}"]`);
const fixed2 = (value) => value.toFixed(2);

// "2x16+1x12" → n1 2, d1 16, n2 1, d2 12: the groups of a face, as the calculator's fields hold them
const groups = (signature) => signature.split("+").map((group) => group.split("x").map(Number));

// The calculator's link (shared/calculator.js readHash): what it leaves out stays the example's.
// In check mode its bar fields go in their order: bottom and top first rows, stirrups (by their
// legs), second rows.
function linkHero(option) {
  const face = (signature) => [0, 1, 2, 3].map((index) => groups(signature)[index] || [0, 0]);
  const [bottom, top] = [face(option.dataset.sig), face(hero.dataset.top)];
  const [, n, d, s] = /^(\d+)x(\d+)@(\d+)$/.exec(hero.dataset.st);
  const bars = [...bottom[0], ...bottom[1], ...top[0], ...top[1], n, d, s, ...bottom[2], ...bottom[3], ...top[2], ...top[3]];
  const params = new URLSearchParams({ v: "2", l: lang, u: units, m: "check", r: bars.join(",") });
  hero.querySelector("#hero-check").href = `beam/#${params}`;
}

function pickOption(option, { animate = true } = {}) {
  const valueOf = (cell) => Number(cell.dataset.value ?? cell.textContent);
  const bottom = valueOf(option.querySelector('[data-hero$="_dcr"]'));
  const [top, shear] = [valueOf(heroCell("top_dcr")), valueOf(heroCell("shear_dcr"))];
  const cells = { bottom_dcr: bottom, dcr: Math.max(bottom, top, shear), sum_flex: Math.max(bottom, top) };
  for (const [name, value] of Object.entries(cells)) {
    const cell = heroCell(name);
    clearTimeout(countUps.get(cell));
    cell.dataset.value = value;
    animate ? tween(cell, Number(cell.textContent), value, fixed2) : (cell.textContent = fixed2(value));
  }
  heroCell("bottom_dcr").previousElementSibling.querySelector("i").style.width = `${Math.min(bottom, 1) * 100}%`;
  heroCell("bottom").textContent = option.querySelector(".val").textContent;
  // the bars marked data-mid are the option's: those between the corners in the metric hero, all the
  // bottom ones in the US hero (#6 and #5 corners); drawn at 5 px a cm (or the US scale), the bottom at y = 300
  const scale = Number(hero.dataset.scale || 5);
  const svg = hero.querySelector(".viz-draw svg g");
  const old = svg.querySelectorAll("[data-mid]");
  const bars = option.dataset.mid.split(";").map((bar) => bar.split(",").map(Number));
  for (const [x, y, d] of bars) {
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    Object.entries({ class: "dw-bar", "data-mid": "", cx: x * scale, cy: 300 - y * scale, r: Math.max(2.5, (d / 2) * scale) })
      .forEach(([name, value]) => circle.setAttribute(name, value));
    if (animate) circle.style.animationDelay = "0s";
    old[0].before(circle);
  }
  old.forEach((circle) => circle.remove());
  linkHero(option);
}

const pickedOption = () => hero.querySelector(".opt:has(input:checked)") || hero.querySelector(".opt");
// on the hero itself: a change of units puts new options in it
hero.addEventListener("change", (event) => { if (event.target.closest(".opt")) pickOption(event.target.closest(".opt")); });

// Sections come in as they are reached, their items one after another. The attribute that hides
// them comes off once they are in, so their own transitions (a card's hover) are theirs again.
const REVEAL = [
  ".steps-band .eyebrow", ".steps-row .step", ".calcs .head", ".cgrid--home > *", ".band-grid > *", ".proof li",
  ".band .band-cols > div", ".pysec > *", ".further .head", ".further-grid > *", ".support > *", ".team > .eyebrow",
  ".team-grid > *",
];
if (moving && "IntersectionObserver" in window) {
  const seen = new IntersectionObserver((entries) => {
    for (const entry of entries.filter((item) => item.isIntersecting)) {
      const element = entry.target;
      seen.unobserve(element);
      element.classList.add("is-in");
      const delay = Number(getComputedStyle(element).getPropertyValue("--i") || 0) * 60;
      setTimeout(() => { element.removeAttribute("data-reveal"); element.classList.remove("is-in"); }, 700 + delay);
    }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });
  for (const selector of REVEAL) {
    document.querySelectorAll(selector).forEach((element, index) => {
      element.dataset.reveal = "";
      element.style.setProperty("--i", String(Math.min(index, 6)));
      seen.observe(element);
    });
  }
  document.documentElement.classList.add("reveal-on");
}

// A figure counts up to itself the first time it is on screen.
function countWhenSeen(element, value) {
  if (!moving || !("IntersectionObserver" in window)) return;
  const watch = new IntersectionObserver(([entry]) => {
    if (!entry.isIntersecting) return;
    watch.disconnect();
    tween(element, 0, value, (current) => String(Math.round(current)), 900);
  }, { threshold: 0.5 });
  watch.observe(element);
}

// ------------------------------------------------------------ language and figures
const strings = await loadStrings("home");
let lang = firstLang;

// The language in the address, and in the links to the calculators, which read it from their hash
// (the hero's own link carries a whole design, and names it itself).
function linkLang() {
  const url = new URL(location.href);
  url.searchParams.set("l", lang);
  history.replaceState(null, "", url);
  for (const link of document.querySelectorAll('a[href^="beam/"]:not(#hero-check), a[href^="slab/"], a[href^="wall/"]')) {
    link.setAttribute("href", `${link.getAttribute("href").split("#")[0]}#l=${lang}`);
  }
}

const langGroup = radiogroup(document.querySelector(".top .seg"), (button) => {
  lang = button.dataset.lang;
  rememberLang(lang);
  units = unitsOf(lang);
  showUnits(units);
  applyStrings(strings, lang, document, units);
  linkHero(pickedOption());
  linkLang();
});
applyStrings(strings, lang, document, units);
linkLang();
langGroup.sync();
// a page come back to may keep the option picked before; the markup shows the first
if (pickedOption() !== hero.querySelector(".opt")) pickOption(pickedOption(), { animate: false });
else linkHero(pickedOption());

// The number comes from mento's CI (shared/stats.json, committed on each mento release).
// Without it the band says "tested against published examples" and shows no figure: never a stale one.
fetch("shared/stats.json", { cache: "no-cache" })
  .then((response) => (response.ok ? response.json() : null))
  .then((stats) => {
    if (!Number.isInteger(stats?.tests) || stats.tests <= 0) return;
    const big = document.getElementById("tests");
    const label = document.getElementById("tests-t");
    big.textContent = String(stats.tests);
    big.hidden = false;
    countWhenSeen(big, stats.tests);
    label.dataset.i18n = "tests_t";
    label.textContent = strings[lang].tests_t;
  })
  .catch(() => { /* no stats yet */ });

// Download, install and compile Python and mento while the visitor reads, and save the result
// (see the saved environment in shared/worker.js), so the calculator only has to start them.
const warm = () => { if (!navigator.connection?.saveData) new Worker("shared/worker.js?module=beam&prefetch=1"); };
"requestIdleCallback" in window ? requestIdleCallback(warm) : setTimeout(warm, 1);
