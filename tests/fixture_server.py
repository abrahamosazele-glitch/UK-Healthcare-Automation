"""
Serves any local fixture directory over HTTP for a scraper under test to
navigate against — shared by the DummyScraper fixtures (tests/fixtures/
dummy_site/) and the NHS Jobs fixtures (tests/fixtures/nhs/).

Uses a real HTTP server (stdlib only) on 127.0.0.1 rather than `file://`
URLs deliberately: Chromium's origin handling for `file://` pages is
inconsistent for things like relative-link navigation, and this needs to
behave like a real website for verification to mean anything. Nothing here
ever leaves the local machine.
"""

from __future__ import annotations

import functools
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator


@contextmanager
def serve_fixture_site(directory: Path, *, path_prefix_map: dict[str, str] | None = None) -> Iterator[str]:
    """`path_prefix_map` lets a fixture site mimic real path-based routing
    (e.g. NHS Jobs' `/candidate/jobadvert/<reference>` detail-page URLs)
    without needing one static file per reference: any request path
    starting with a mapped prefix is served from the mapped file instead,
    while the reference itself stays in the URL exactly as production code
    would see it."""
    prefix_map = path_prefix_map or {}

    class _Handler(SimpleHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib method name
            path_only = self.path.split("?", 1)[0]
            for prefix, target in prefix_map.items():
                if path_only.startswith(prefix):
                    self.path = target
                    break
            super().do_GET()

    handler_cls = functools.partial(_Handler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
