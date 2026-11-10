import http.client
import threading
from functools import partial
from http.server import ThreadingHTTPServer

import pytest
from click.testing import CliRunner

from webtim.cli import main
from webtim.serve import Handler, choose_encoding, parse_accept_encoding


@pytest.fixture
def served(site_dir, tmp_path):
    out = tmp_path / "out"
    assert CliRunner().invoke(main, [str(site_dir), "-o", str(out), "--hash"]).exit_code == 0
    (out / "docs" / "index.html").write_text("<html><body>docs index</body></html>")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(out)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield out, f"127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def request(base, path, encoding=None, method="GET"):
    host, port = base.split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    headers = {"Accept-Encoding": encoding} if encoding is not None else {}
    conn.request(method, path, headers=headers)
    response = conn.getresponse()
    body = response.read()
    result = response.status, {key.lower(): value for key, value in response.getheaders()}, body
    conn.close()
    return result


def test_q_zero_disables_an_encoding(served):
    _, base = served
    status, headers, _ = request(base, "/index.html", "gzip;q=0")
    assert status == 200 and "content-encoding" not in headers


def test_explicit_preference_beats_default_order(served):
    _, base = served
    assert request(base, "/index.html", "br;q=0, gzip;q=1")[1]["content-encoding"] == "gzip"
    assert request(base, "/index.html", "gzip;q=0.4, br;q=1")[1]["content-encoding"] == "br"
    assert request(base, "/index.html", "gzip;q=0.9, br;q=1")[1]["content-encoding"] == "br"


def test_wildcard_selects_the_smallest_available(served):
    _, base = served
    assert request(base, "/index.html", "*")[1]["content-encoding"] == "br"


def test_wildcard_q_zero_and_malformed_q(served):
    _, base = served
    assert request(base, "/index.html", "*;q=0")[0] == 406
    assert "content-encoding" not in request(base, "/index.html", "gzip;q=abc")[1]


def test_identity_q_zero_is_not_acceptable(served):
    _, base = served
    assert request(base, "/index.html", "identity;q=0")[0] == 406
    assert request(base, "/index.html", "identity;q=0, gzip")[1]["content-encoding"] == "gzip"


def test_head_reports_sidecar_size_without_a_body(served):
    out, base = served
    status, headers, body = request(base, "/index.html", "br", "HEAD")
    assert status == 200 and body == b""
    assert headers["content-encoding"] == "br"
    assert int(headers["content-length"]) == (out / "index.html.br").stat().st_size


def test_query_string_is_served_compressed(served):
    _, base = served
    assert request(base, "/index.html?v=2", "gzip")[1]["content-encoding"] == "gzip"


def test_directory_without_slash_redirects_and_index_is_served(served):
    _, base = served
    status, headers, _ = request(base, "/docs")
    assert status == 301 and headers["location"].endswith("/docs/")
    status, headers, body = request(base, "/docs/")
    assert status == 200 and b"docs index" in body


def test_vary_and_cache_headers_on_every_response(served):
    out, base = served
    assert request(base, "/index.html")[1]["vary"] == "Accept-Encoding"
    assert request(base, "/index.html")[1]["cache-control"] == "no-cache"
    css = next((out / "css").glob("app.*.css")).name
    assert request(base, f"/css/{css}")[1]["cache-control"] == "public, max-age=31536000, immutable"


def test_choose_encoding_prefers_highest_q_then_brotli():
    assert choose_encoding("gzip", {"br", "gzip"}) == "gzip"
    assert choose_encoding("gzip, br", {"br", "gzip"}) == "br"
    assert choose_encoding("br;q=0.2, gzip;q=0.8", {"br", "gzip"}) == "gzip"
    assert choose_encoding("br;q=0, gzip;q=0", {"br", "gzip"}) is None
    assert choose_encoding("", {"br", "gzip"}) is None
    assert choose_encoding("gzip", {"br"}) is None


def test_parse_accept_encoding_handles_duplicates_and_case():
    weights = parse_accept_encoding(" GZIP;q=0.5, gzip;q=0.9 , Br ")
    assert weights["gzip"] == 0.9 and weights["br"] == 1.0
