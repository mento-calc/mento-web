// Every UI string lives in shared/i18n/<page>.json as {"es": {key: text}, "en": {...}}.
// The markup carries the Spanish text as its first paint; py/test_i18n.py keeps the two in step.
//   data-i18n="k"          textContent
//   data-i18n-html="k"     innerHTML (strings with <i class="sym"> and the like)
//   data-i18n-aria="k"     aria-label
//   data-i18n-content="k"  content (meta description)
// A text may name units as tokens ({u_len}, {u_moment}...), filled in for the page's system, and a
// key may have a US variant ("barhint_us") that stands in for it when the page speaks US units.

const LANG_KEY = "mento-lang";
const UNITS_KEY = "mento-units";
export const LANGS = ["es", "en"];
export const SYSTEMS = ["si", "us"];

// The only visitors the site does not greet in Spanish and SI: a browser set to US English, on its
// first visit, gets English and US units. After that, each is what the visitor last picked.
const american = () => {
  try { return navigator.language === "en-US"; } catch { return false; }
};

export function preferredLang() {
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (LANGS.includes(saved)) return saved;
  } catch { /* storage blocked */ }
  return american() ? "en" : "es";
}

export function rememberLang(lang) {
  try { localStorage.setItem(LANG_KEY, lang); } catch { /* storage blocked */ }
}

export function preferredUnits() {
  try {
    const saved = localStorage.getItem(UNITS_KEY);
    if (SYSTEMS.includes(saved)) return saved;
  } catch { /* storage blocked */ }
  return american() ? "us" : "si";
}

export function rememberUnits(units) {
  try { localStorage.setItem(UNITS_KEY, units); } catch { /* storage blocked */ }
}

// How each system writes the units a text names. The words ({u_bar}) are the dictionary's own.
export const UNIT_WORDS = {
  si: { len: "cm", cov: "mm", span: "cm", force: "kN", moment: "kNm" },
  us: { len: "in", cov: "in", span: "ft", force: "kip", moment: "kip-ft" },
};

export async function loadStrings(page) {
  const response = await fetch(new URL(`i18n/${page}.json`, import.meta.url));
  if (!response.ok) throw new Error(`i18n/${page}.json: HTTP ${response.status}`);
  return response.json();
}

// The dictionary of one language in one system: US variants in, unit tokens filled.
export function resolveStrings(strings, lang, units = "si") {
  const base = strings[lang] || strings.es;
  const pick = (key) => (units === "us" && `${key}_us` in base ? base[`${key}_us`] : base[key]);
  const fill = (text) => text.replace(/\{u_(\w+)\}/g, (token, name) => UNIT_WORDS[units][name] ?? pick(`u_${name}`) ?? token);
  return Object.fromEntries(Object.keys(base).filter((key) => !key.endsWith("_us"))
    .map((key) => [key, typeof pick(key) === "string" ? fill(pick(key)) : pick(key)]));
}

export function applyStrings(strings, lang, root = document, units = "si") {
  const t = resolveStrings(strings, lang, units);
  const text = (key) => {
    if (key in t) return t[key];
    console.warn(`i18n: no "${key}" in ${lang}`);
    return resolveStrings(strings, "es", units)[key];
  };
  if (root === document) document.documentElement.lang = lang;
  root.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = text(el.dataset.i18n); });
  root.querySelectorAll("[data-i18n-html]").forEach((el) => { el.innerHTML = text(el.dataset.i18nHtml); });
  root.querySelectorAll("[data-i18n-aria]").forEach((el) => { el.setAttribute("aria-label", text(el.dataset.i18nAria)); });
  root.querySelectorAll("[data-i18n-content]").forEach((el) => { el.setAttribute("content", text(el.dataset.i18nContent)); });
  root.querySelectorAll("[data-lang]").forEach((el) => { el.setAttribute("aria-checked", String(el.dataset.lang === lang)); });
  return t;
}
