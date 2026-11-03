"""Serve the trip map over localhost so the map tiles load.

OSM's tile servers require a Referer header, which a file:// page does not send.

Run: python -m barrierfrei.serve   (re-open the last trip map)
"""

import argparse
import functools
import logging
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from barrierfrei.config import get_settings

log = logging.getLogger("barrierfrei.serve")


class QuietHandler(SimpleHTTPRequestHandler):
    """Serves the map page quietly: one DEBUG line per request, not INFO."""

    def log_message(self, format: str, *args: object) -> None:
        logging.getLogger("barrierfrei.serve").debug(format % args)


def start(directory: Path, port: int = 8765) -> tuple[ThreadingHTTPServer, str]:
    """Bind localhost to serve directory; fall back to a free port if taken."""
    handler = functools.partial(QuietHandler, directory=str(directory))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def serve(directory: Path, page: str = "plan.html", port: int = 8765, open_browser: bool = True) -> None:
    """Serve directory until Ctrl+C, opening the map page in the browser."""
    server, url = start(directory, port)
    log.info("Map: %s%s  (Ctrl+C to stop)", url, page)
    if open_browser:
        # A foreground BROWSER command can block until it exits, so never let it delay serving.
        threading.Thread(target=webbrowser.open, args=(f"{url}{page}",), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dir", dest="directory", type=Path, default=get_settings().data_dir / "plans")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    serve(args.directory, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
