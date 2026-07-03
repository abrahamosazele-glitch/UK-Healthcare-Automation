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
def serve_fixture_site(directory: Path) -> Iterator[str]:
    handler_cls = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
