import base64
import re
from pathlib import PurePosixPath as P

from webtim import transforms as T
from tests.conftest import png


def test_inline_small_replaces_and_drops_unused_file():
    icon = png((2, 2))
    site = {P("index.html"): b'<img src="a.png">', P("a.png"): icon}
    assert T.inline_small(site, 5000) == 1
    assert base64.b64encode(icon) in site[P("index.html")]
    assert P("a.png") not in site


def test_inline_small_respects_threshold_and_nested_paths():
    big = png((300, 300, ), (1, 2, 3, 255))
    site = {P("docs/p.html"): b'<img src="../a.png">', P("a.png"): big}
    assert T.inline_small(site, 10) == 0
    assert P("a.png") in site


def test_inline_small_keeps_file_still_used_elsewhere():
    site = {P("a.html"): b'<img src="x.png">', P("b.html"): b'<img src="x.png?v=2">', P("x.png"): png((2, 2))}
    T.inline_small(site, 5000)
    assert P("x.png") not in site  # both pages inlined it, nothing references it any more
    site = {P("a.html"): b'<img src="x.png">', P("m.js"): b'load("x.png")', P("x.png"): png((2, 2))}
    T.inline_small(site, 5000)
    assert P("x.png") in site  # the JS still names it


def test_inline_svg_is_url_encoded_not_base64():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'
    site = {P("a.html"): b'<img src="i.svg">', P("i.svg"): svg}
    T.inline_small(site, 5000)
    html = site[P("a.html")].decode()
    assert "data:image/svg+xml," in html and "base64" not in html and "%3Csvg" in html


def test_defer_adds_to_external_classic_only():
    html = b'<script src="a.js"></script><script src="b.js" async></script><script type="module" src="c.js"></script><script src="d.js" defer></script>'
    site = {P("i.html"): html}
    assert T.defer_scripts(site) == 1
    out = site[P("i.html")].decode()
    assert '<script src="a.js" defer>' in out
    assert out.count("defer") == 2  # a.js (new) + d.js (already there); module and async untouched


def test_defer_skips_pages_with_inline_code_but_not_data_blocks():
    unsafe = {P("i.html"): b'<script src="a.js"></script><script>a.init()</script>'}
    assert T.defer_scripts(unsafe) == 0 and b"defer" not in unsafe[P("i.html")]
    ok = {P("i.html"): b'<script src="a.js"></script><script type="application/ld+json">{"a":1}</script>'}
    assert T.defer_scripts(ok) == 1


def test_defer_does_not_mistake_a_filename_for_an_attribute():
    """`defer-dep.js` is a URL, not an existing defer attribute."""
    site = {P("i.html"): b'<script src="defer-dep.js"></script><script src="async-use.js"></script>'}
    assert T.defer_scripts(site) == 2
    out = site[P("i.html")].decode()
    assert '<script src="defer-dep.js" defer>' in out
    assert '<script src="async-use.js" defer>' in out


def test_hash_names_rewrites_relative_and_root_refs():
    site = {
        P("index.html"): b'<link href="css/app.css"><script src="/js/app.js"></script>',
        P("css/app.css"): b"a{background:url(../img/i.png)}",
        P("js/app.js"): b"x()",
        P("img/i.png"): png((2, 2)),
    }
    T.hash_names(site)
    names = {p.name for p in site}
    assert not names & {"app.css", "app.js", "i.png"}
    html = site[P("index.html")].decode()
    for p in site:
        if p.suffix in {".css", ".js"}:
            assert p.name in html
    css = next(v for k, v in site.items() if k.suffix == ".css").decode()
    png_name = next(p.name for p in site if p.suffix == ".png")
    assert f"../img/{png_name}" in css
    # the css hash must cover the *rewritten* css, so changing the image changes the css name too
    assert re.fullmatch(r"app\.[0-9a-f]{8}\.css", next(p.name for p in site if p.suffix == ".css"))


def test_hash_is_deterministic_and_content_sensitive():
    a = {P("a.js"): b"1", P("i.html"): b'<script src="a.js">'}
    b = {P("a.js"): b"1", P("i.html"): b'<script src="a.js">'}
    c = {P("a.js"): b"2", P("i.html"): b'<script src="a.js">'}
    for s in (a, b, c):
        T.hash_names(s)
    assert set(a) == set(b) != set(c)


def test_hash_skips_js_imported_by_other_js():
    site = {P("i.html"): b'<script type="module" src="main.js">', P("main.js"): b'import "./util.js"', P("util.js"): b"export {}"}
    T.hash_names(site)
    assert P("util.js") in site  # renaming it would break main.js's import
    assert P("main.js") not in site  # nothing imports main.js, so it is safely hashed


def test_absolute_url_reference_skips_hashing_without_duplicating():
    site = {P("i.html"): b'<img src="b.png"><meta content="https://x.com/b.png">', P("b.png"): png((2, 2))}
    skipped = {}
    T.hash_names(site, skipped=skipped)
    assert list(site) == [P("i.html"), P("b.png")]  # one copy, still resolvable by both refs
    assert P("b.png") in skipped


def test_absolute_css_reference_skips_hashing():
    site = {P("i.html"): b'<link href="s.css">', P("s.css"): b'body{background:url(https://x.com/bg.png)}',
            P("bg.png"): png((2, 2))}
    skipped = {}
    T.hash_names(site, skipped=skipped)
    assert P("bg.png") in site and P("bg.png") in skipped
    css = next(p for p in site if p.suffix == ".css")
    assert "https://x.com/bg.png" in site[css].decode()


def test_runtime_built_path_skips_hashing():
    for js in (b'src = `img/${name}.png`', b'src = "img/" + name + ".png"'):
        site = {P("index.html"): b'<img src="img/a.png">', P("app.js"): js, P("img/a.png"): png((2, 2))}
        skipped = {}
        T.hash_names(site, skipped=skipped)
        assert P("img/a.png") in site and P("img/a.png") in skipped


def test_runtime_built_path_skips_webp():
    site = {P("index.html"): b'<img src="img/a.png">', P("app.js"): b'src = `img/${name}.png`',
            P("img/a.png"): png((300, 300))}
    skipped = {}
    assert T.to_webp(site, skipped=skipped) == {}
    assert P("img/a.png") in site and P("img/a.png") in skipped


def test_webp_converts_and_rewrites():
    site = {P("i.html"): b'<img src="big.png">', P("big.png"): png((400, 200))}
    assert T.to_webp(site) == {P("big.png"): P("big.webp")}
    assert b"big.webp" in site[P("i.html")] and site[P("big.webp")][:4] == b"RIFF"
    assert P("big.png") not in site


def test_webp_leaves_image_alone_when_not_smaller():
    import io
    import os

    from PIL import Image
    buf = io.BytesIO()  # 16x16 random noise: lossless WebP (1030 B) is bigger than the PNG (852 B)
    Image.frombytes("RGB", (16, 16), os.urandom(16 * 16 * 3)).save(buf, "PNG")
    noise = buf.getvalue()
    site = {P("i.html"): b'<img src="n.png">', P("n.png"): noise}
    assert T.to_webp(site) == {}
    assert site[P("n.png")] == noise and b"n.png" in site[P("i.html")]
