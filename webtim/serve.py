"""Stdlib static server for small panels: serves .br/.gz sidecars and sets cache headers.

    webtim-serve OUTPUT_DIR [--port 8000]          # or: python -m webtim.serve

Meant for local/small deployments. For production traffic prefer nginx or Caddy (see serve_config.py).
"""
import os
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import click

from webtim import __version__
from webtim.branding import BrandedCommand, show_banner
from webtim.serve_config import LONG
from webtim.transforms import HASHED_RE

SIDECARS = (("br", ".br"), ("gzip", ".gz"))


def parse_accept_encoding(header: str) -> dict:
    """Map encoding -> q from an Accept-Encoding header. `q=0` means "must not be used"."""
    weights = {}
    for part in (header or "").split(","):
        token, _, rest = part.partition(";")
        name = token.strip().lower()
        if not name:
            continue
        q = 1.0
        for param in rest.split(";"):
            key, _, value = param.partition("=")
            if key.strip().lower() == "q":
                try:
                    q = float(value.strip())
                except ValueError:
                    q = 0.0
        weights[name] = max(weights.get(name, 0.0), min(max(q, 0.0), 1.0))
    return weights


def accepts_identity(header: str) -> bool:
    """Whether the response may use no content coding, per the explicit identity/wildcard q-values."""
    weights = parse_accept_encoding(header)
    if not weights:
        return True
    if "identity" in weights:
        return weights["identity"] > 0
    if "*" in weights:
        return weights["*"] > 0
    return True


def choose_encoding(header: str, available) -> str | None:
    """Pick the acceptable encoding with the highest q; `br` wins a tie. None when nothing qualifies.

    `*` covers every encoding the client did not name. An explicit `q=0` (or a header that names
    only encodings we do not have) always wins over the wildcard, per RFC 9110.
    """
    weights = parse_accept_encoding(header)
    if not weights:
        return None
    star = weights.get("*", 0.0)
    best, best_q = None, 0.0
    for enc, _ in SIDECARS:
        if enc not in available:
            continue
        q = weights.get(enc, star)
        if q <= 0:
            continue
        if q > best_q or (q == best_q and enc == "br"):
            best, best_q = enc, q
    return best


class Handler(SimpleHTTPRequestHandler):
    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path) and self.path.split("?")[0].endswith("/"):
            path = os.path.join(path, "index.html")
        accepts = self.headers.get("Accept-Encoding", "")
        available = {
            enc for enc, ext in SIDECARS
            if os.path.isfile(path) and os.path.isfile(path + ext)
        }
        encoding = choose_encoding(accepts, available)
        if encoding is not None:
            ext = next(ext for enc, ext in SIDECARS if enc == encoding)
            f = open(path + ext, "rb")
            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Length", str(os.fstat(f.fileno()).st_size))
            self.send_header("Content-Encoding", encoding)
            self.end_headers()
            return f
        if not accepts_identity(accepts):
            self.send_error(406, "No acceptable content coding")
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("Vary", "Accept-Encoding")
        hashed = re.search(HASHED_RE, self.path.split("?")[0])
        self.send_header("Cache-Control", LONG if hashed else "no-cache")
        super().end_headers()


@click.command(cls=BrandedCommand)
@click.version_option(__version__, prog_name="webtim-serve")
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option("--port", default=8000, show_default=True)
@click.option("--host", default="127.0.0.1", show_default=True)
def main(directory, port, host):
    show_banner()
    click.echo(f"Serving {directory} on http://{host}:{port}")
    ThreadingHTTPServer((host, port), partial(Handler, directory=directory)).serve_forever()


if __name__ == "__main__":
    main()
