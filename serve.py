"""Serve the site locally, the way it is served in production: every file revalidated.

``python -m http.server`` sends no Cache-Control, so browsers cache by heuristic and can keep an
old worker.js or glue module long after it changed. This is the same server, telling them not to::

    python serve.py [port]

To try a mento that is not on PyPI yet, give it the wheel (``pip wheel --no-deps <mento checkout>``)::

    python serve.py [port] --wheel mento-1.6.0-py3-none-any.whl

On localhost the worker asks for ``dev/mento.json`` and, when this server answers, installs that
wheel in place of the pinned release (shared/worker.js). Nothing is copied into the site.
"""

import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class NoCacheHandler(SimpleHTTPRequestHandler):
    wheel: Path | None = None

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def translate_path(self, path: str) -> str:
        """``/dev/<the wheel's name>`` is the file given with ``--wheel``, wherever it lives."""
        if self.wheel and path.split("?")[0] == f"/dev/{self.wheel.name}":
            return str(self.wheel)
        return super().translate_path(path)

    def do_GET(self) -> None:
        if not self.wheel or self.path.split("?")[0] != "/dev/mento.json":
            return super().do_GET()
        # the time it was built is part of what the worker saves its environment under: a rebuilt
        # wheel is installed again
        body = json.dumps({"wheel": self.wheel.name, "stamp": int(self.wheel.stat().st_mtime)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serve mento-web locally.")
    parser.add_argument("port", nargs="?", type=int, default=8765)
    parser.add_argument(
        "--wheel", type=Path, help="a mento wheel to run instead of the release pinned in shared/worker.js"
    )
    args = parser.parse_args()
    if args.wheel:
        NoCacheHandler.wheel = args.wheel.resolve(strict=True)
        print(f"running {NoCacheHandler.wheel.name} instead of the pinned mento")
    handler = partial(NoCacheHandler, directory=str(Path(__file__).parent))
    print(f"mento-web on http://localhost:{args.port}")
    ThreadingHTTPServer(("", args.port), handler).serve_forever()
