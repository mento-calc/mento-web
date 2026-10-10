# mento-web

Free reinforced concrete calculators that run [mento](https://github.com/mento-calc/mento)
in your browser: **[mentocalc.com](https://mentocalc.com)**

Beams, slabs and walls, checked against ACI 318-19, CIRSOC 201-25 and EN 1992. Nothing to
install, and nothing you type leaves your device: Python runs in the browser through
[Pyodide](https://pyodide.org). In Spanish and English.

## Run locally

```bash
python serve.py
```

Open <http://localhost:8765>.

## Test

```bash
pip install -r requirements-dev.txt
pytest
```

## Feedback and support

- Found a result that differs from yours? [Open an issue](https://github.com/mento-calc/mento/issues/new).
- Missing a calculator? [Ask for it](https://github.com/mento-calc/mento/discussions).
- mento is free and ad-free. If it saved you an afternoon, you can support it on
  [Ko-fi](https://ko-fi.com/mentoapp), [Cafecito](https://cafecito.app/mentoapp) or
  [GitHub Sponsors](https://github.com/sponsors/mihdicaballero).

## Disclaimer

Provided without warranty. Results must be reviewed by a licensed professional, who remains
responsible for the design.

MIT licensed.
