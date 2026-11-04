# CLAUDE.md — mento-web

Static site that runs the `mento` Python package in the browser via Pyodide. No backend, no
build step, vanilla JS. `README.md` is kept basic on purpose; the code is the reference.

- Python for tests and the local server (Windows): `C:\Users\mihdi\anaconda3\envs\rame-env\python.exe`.
  Tests: `python -m pytest`. Lint: `python -m ruff check . && python -m ruff format --check .`
- The site runs the mento **published on PyPI** (`MENTO_VERSION` in `shared/worker.js`), not a
  local checkout. Keep `requirements-dev.txt` and the `#version` span of the four pages in step
  with it. The pin moves only once that release is on PyPI.
- To work against a mento that is not released yet: build its wheel (`pip wheel --no-deps <mento>`),
  run the site with `python serve.py --wheel <file>` (localhost only) and the tests with that wheel
  installed in a scratch folder on `PYTHONPATH`.
- `py/*.py` must stay free of Pyodide-specific code so it can be tested from a normal interpreter.
- Read mento results through its public dataclasses (`beam.flexure_design`, `beam.shear_design`,
  `beam.reinforcement`), never `beam._*`.
- UX principle: zero friction. Prefilled example, recalculates as you type, clear verdict,
  shareable link, the equivalent Python snippet as a bridge to the package. Mobile first; ES and EN.
- Verify UI changes in a browser at 375 px and desktop widths; the first load takes ~20 s.
