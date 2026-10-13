// Runs Python (Pyodide) and mento off the main thread, so the page never freezes.
// Each calculator starts it as `worker.js?module=<name>`, which loads `py/<name>.py`.
// A glue module exposes run(json) -> json, report(json) -> json and an EXAMPLE dict.
// With `&prefetch=1` (the home page) it builds the saved environment (below) if there is none, and quits.
// Protocol: {id, type: "run" | "report" | "summary", payload} -> {id, ok, result | error}
// plus unsolicited {type: "progress", step} and {type: "ready", version} on startup.

const PYODIDE_VERSION = "0.29.5";
const MENTO_VERSION = "1.6.1";
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const PYODIDE_PACKAGES = ["numpy", "pandas", "jinja2", "lxml"];
// mento's own dependencies that Pyodide does not ship (it is installed with deps=False).
const PYPI_PACKAGES = ["pint", "tabulate", "python-docx"];

const MODULE = new URL(self.location.href).searchParams.get("module");
if (!/^[a-z_]+$/.test(MODULE || "")) throw new Error(`Unknown calculator module: ${MODULE}`);
const PREFETCH = new URL(self.location.href).searchParams.has("prefetch");

importScripts(`${PYODIDE_URL}pyodide.js`);

const progress = (step) => self.postMessage({ type: "progress", step });

// mento imports IPython, matplotlib and seaborn at the top of its modules, but only uses them to
// render Markdown in notebooks and to plot. The page draws the section itself, so stubs stand in
// for all three: ~25 MB less to download and ~3 s less of Python imports on every visit.
const STUBS = `
import importlib.abc, importlib.machinery, sys, types

class _AnythingMeta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return cls

class _Anything(metaclass=_AnythingMeta):
    """Accepts any call or attribute: sns.set_theme(...), plt.rcParams.update(...), class Foo(Figure)."""
    def __init__(self, *args, **kwargs): pass
    def __call__(self, *args, **kwargs): return self
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self

class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Anything

class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("matplotlib", "seaborn"):
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
    def create_module(self, spec): return _StubModule(spec.name)
    def exec_module(self, module): module.__version__ = "99"  # pandas checks it when it probes for matplotlib

sys.meta_path.insert(0, _StubFinder())

ipython = types.ModuleType("IPython")
display = types.ModuleType("IPython.display")
class Markdown(str):
    def __new__(cls, data=""):
        return super().__new__(cls, data)
display.Markdown = Markdown
display.display = lambda *args, **kwargs: None
ipython.display = display
ipython.get_ipython = lambda: None  # pandas asks whether it runs in a notebook
ipython.__version__ = "0"
sys.modules["IPython"] = ipython
sys.modules["IPython.display"] = display
`;

// The saved environment. Downloads come from the HTTP cache after the first visit, but two costs
// came back on every one: micropip asking PyPI what to install, and Python compiling each module it
// imports (pandas, mento, pint...) because Pyodide keeps no __pycache__ between visits, ~2 s on a
// desktop and three times that on a phone. So the first boot saves in Cache Storage, as a tar,
// everything it added to site-packages after the Pyodide packages: the PyPI packages, the bytecode
// of every module imported, and pint's parsed unit definitions. Later boots unpack it and skip
// micropip, PyPI and the compiler. The key carries every version, so a new mento (or Pyodide, or
// package list) misses, rebuilds, and the old entry is deleted.
const ENV_CACHE = "mento-env";
const envKey = (mento) => new URL(
  `../mento-env/${PYODIDE_VERSION}/${mento}/${encodeURIComponent(PYPI_PACKAGES.join(" "))}.tar`,
  self.location.href,
).href;
let ENV_KEY = envKey(MENTO_VERSION);

// A mento that is not on PyPI yet, tried on the site before its release: `python serve.py --wheel
// <file>` answers dev/mento.json with the wheel to install in place of the pinned one. Asked only
// on localhost, so the deployed site never makes the request.
async function devWheel() {
  if (!["localhost", "127.0.0.1"].includes(self.location.hostname)) return null;
  try {
    const response = await fetch(new URL("../dev/mento.json", self.location.href), { cache: "no-store" });
    const dev = response.ok ? await response.json() : null;
    return dev && { stamp: dev.stamp, url: new URL(`../dev/${dev.wheel}`, self.location.href).href };
  } catch {
    return null;
  }
}

const ENV = `
import os, sys, sysconfig, tarfile, time

SITE = sysconfig.get_path("purelib")
PINT_CACHE = os.path.join(SITE, "_pint_cache")

def env_start():
    """Before installing: note the time, and let imports write their bytecode."""
    sys.dont_write_bytecode = False
    return time.time()

def env_pint_cache():
    """mento builds UnitRegistry(system="mks"); keep pint's parsed definitions with the rest."""
    import pint
    os.makedirs(PINT_CACHE, exist_ok=True)
    init = pint.UnitRegistry.__init__
    def cached(self, *args, **kwargs):
        kwargs.setdefault("cache_folder", PINT_CACHE)
        init(self, *args, **kwargs)
    pint.UnitRegistry.__init__ = cached

def env_pack(since, path):
    """Tar what changed in site-packages since env_start(), with every .pyc made unchecked."""
    import compileall, py_compile
    # What the other calculators import lazily from mento, compiled now so it is saved too.
    compileall.compile_dir(os.path.join(SITE, "mento"), quiet=2,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    with tarfile.open(path, "w") as tar:
        for root, _, files in os.walk(SITE):
            for name in files:
                full = os.path.join(root, name)
                if os.stat(full).st_mtime < since:
                    continue
                if name.endswith(".pyc"):
                    # Imports write the source's mtime into the header, and Pyodide re-extracts
                    # pandas with a new one on every visit. Flags = 1 (PEP 552): hash-based,
                    # never checked, so the bytecode stays valid; the key already versions it.
                    with open(full, "r+b") as f:
                        f.seek(4)
                        f.write((1).to_bytes(4, "little"))
                tar.add(full, arcname=os.path.relpath(full, SITE))

def env_unpack(path):
    with tarfile.open(path) as tar:
        tar.extractall(SITE, filter="tar")
`;

async function readEnv() {
  try {
    const response = await (await caches.open(ENV_CACHE)).match(ENV_KEY);
    return response ? new Uint8Array(await response.arrayBuffer()) : null;
  } catch {
    return null;  // no Cache Storage (private mode, file://): boot the long way
  }
}

async function saveEnv(pyodide, since) {
  try {
    pyodide.globals.get("env_pack")(since, "/tmp/env.tar");
    const cache = await caches.open(ENV_CACHE);
    await cache.put(ENV_KEY, new Response(pyodide.FS.readFile("/tmp/env.tar"), {
      headers: { "Content-Type": "application/x-tar" },
    }));
    for (const request of await cache.keys()) if (request.url !== ENV_KEY) await cache.delete(request);
  } catch (error) {
    console.warn("mento: could not save the environment", error);
  }
}

async function boot(saved, dev) {
  progress("python");
  const pyodide = await loadPyodide({ indexURL: PYODIDE_URL });

  progress("packages");
  await pyodide.loadPackage(saved ? PYODIDE_PACKAGES : [...PYODIDE_PACKAGES, "micropip"]);
  pyodide.runPython(ENV);

  progress("mento");
  let since = null;
  if (saved) {
    pyodide.FS.writeFile("/tmp/env.tar", saved);
    pyodide.globals.get("env_unpack")("/tmp/env.tar");
  } else {
    since = pyodide.globals.get("env_start")();
    const micropip = pyodide.pyimport("micropip");
    await micropip.install(PYPI_PACKAGES);
    await micropip.install.callKwargs([dev ? dev.url : `mento==${MENTO_VERSION}`], { deps: false });
  }

  progress("warmup");
  pyodide.runPython(STUBS);
  pyodide.globals.get("env_pint_cache")();
  // py/common.py holds what the calculators share, so it travels with every one of them.
  for (const name of ["common", MODULE]) {
    // Revalidated every time: a glue module a release older than the page breaks the import.
    const response = await fetch(new URL(`../py/${name}.py`, self.location.href), { cache: "no-cache" });
    if (!response.ok) throw new Error(`py/${name}.py: HTTP ${response.status}`);
    pyodide.FS.writeFile(`/home/pyodide/${name}.py`, await response.text());
  }
  const api = pyodide.pyimport(MODULE);
  // The first call imports what mento loads lazily. On a first boot it runs here, so those modules
  // are compiled before the tar is made; with a saved environment they load as fast as the rest,
  // and running the example here would only put a second calculation before the page's own.
  if (!saved) api.run(JSON.stringify(api.EXAMPLE.toJs({ dict_converter: Object.fromEntries })));
  // a wheel names its own version; the pinned release is the one the page already shows
  const version = dev ? pyodide.runPython("import mento; mento.__version__") : MENTO_VERSION;
  return { api, pyodide, version, save: since === null ? null : () => saveEnv(pyodide, since) };
}

async function start() {
  const dev = await devWheel();
  if (dev) ENV_KEY = envKey(`dev-${dev.stamp}`);
  const saved = await readEnv();
  if (saved && PREFETCH) return null;
  if (!saved) return boot(null, dev);
  try {
    return await boot(saved, dev);
  } catch (error) {
    // A saved environment that no longer boots is dropped and rebuilt, not shown as an error.
    console.warn("mento: saved environment failed, rebuilding", error);
    await caches.open(ENV_CACHE).then((cache) => cache.delete(ENV_KEY)).catch(() => {});
    return boot(null, dev);
  }
}

const apiPromise = start().then(
  async (booted) => {
    if (PREFETCH) {
      await booted?.save?.();
      return self.close();
    }
    self.postMessage({ type: "ready", version: booted.version });
    // After "ready", so the first visit does not wait for it; the first run queues behind the tar.
    booted.save?.();
    return booted;
  },
  (error) => {
    self.postMessage({ type: "fatal", error: String(error) });
    throw error;
  },
);

// The Excel of a list of beams is written by pandas with openpyxl, which almost nobody needs: it
// is fetched the first time one is asked for, not on every visit (it is not in PYODIDE_PACKAGES).
let excelWriter = null;
function loadExcelWriter(pyodide) {
  excelWriter ??= pyodide.loadPackage("openpyxl").catch(async () => {
    await pyodide.loadPackage("micropip");
    await pyodide.pyimport("micropip").install("openpyxl");
  });
  excelWriter.catch(() => { excelWriter = null; });  // a failed attempt is tried again next time
  return excelWriter;
}

self.onmessage = async ({ data }) => {
  const { id, type, payload } = data;
  try {
    const { api, pyodide } = await apiPromise;
    if (type === "summary") await loadExcelWriter(pyodide);
    const fn = { report: api.report, summary: api.summary }[type] ?? api.run;
    self.postMessage({ id, ok: true, result: JSON.parse(fn(JSON.stringify(payload))) });
  } catch (error) {
    self.postMessage({ id, ok: false, error: String(error) });
  }
};
