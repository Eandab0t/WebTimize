import hashlib
import io
from pathlib import PurePosixPath as P
from urllib.parse import quote

from PIL import Image

from tests.conftest import banner_png, png
from webtim import transforms as T


def jpeg(size=(64, 64), color=(10, 120, 200), quality=90) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def test_percent_encoded_reference_is_rewritten_after_hashing():
    site = {P("index.html"): b'<img src="img/my%20file.png">', P("img/my file.png"): png((4, 4))}
    T.hash_names(site)
    html = site[P("index.html")].decode()
    renamed = next(p for p in site if p.suffix == ".png")
    assert renamed.name.startswith("my file.")
    assert f"img/{quote(renamed.name)}" in html  # the reference stays percent-encoded
    assert "img/my%20file.png" not in html


def test_percent_encoded_root_relative_reference_is_rewritten():
    site = {P("docs/page.html"): b'<img src="/img/my%20file.png">', P("img/my file.png"): png((4, 4))}
    T.hash_names(site)
    renamed = next(p for p in site if p.suffix == ".png")
    assert f"/img/{quote(renamed.name)}" in site[P("docs/page.html")].decode()


def test_percent_encoded_reference_resolves_for_inlining():
    site = {P("index.html"): b'<img src="img/my%20file.png">', P("img/my file.png"): png((2, 2))}
    assert T.inline_small(site, 5000) == 1
    assert b"data:image/png;base64," in site[P("index.html")]
    assert P("img/my file.png") not in site  # nothing references it any more


def test_webp_never_overwrites_an_existing_webp():
    site = {P("i.html"): b'<img src="a.png">', P("a.png"): banner_png(), P("a.webp"): b"existing"}
    skipped = {}
    assert T.to_webp(site, skipped=skipped) == {}
    assert site[P("a.webp")] == b"existing"
    assert P("a.png") in site and P("a.png") in skipped
    assert "already exists" in skipped[P("a.png")]


def test_webp_does_not_let_two_sources_claim_one_target():
    site = {P("i.html"): b'<img src="a.png"><img src="a.jpg">',
            P("a.png"): banner_png(), P("a.jpg"): jpeg()}
    moves = T.to_webp(site)
    assert len(moves) == 1
    assert sum(1 for p in site if p.suffix == ".webp") == 1


def test_hash_never_overwrites_an_existing_hashed_name():
    data = png((2, 2))
    name = f"a.{hashlib.sha256(data).hexdigest()[:8]}.png"
    site = {P("i.html"): b'<img src="a.png">', P("a.png"): data, P(name): b"other"}
    skipped = {}
    T.hash_names(site, skipped=skipped)
    assert P("a.png") in site and site[P("a.png")] == data  # never silently replaced
    assert "already exists" in skipped[P("a.png")]
    assert b"other" in site.values()  # the colliding asset survives under its own new name


def test_hash_records_why_code_referenced_assets_are_not_renamed():
    site = {P("i.html"): b'<script type="module" src="main.js">',
            P("main.js"): b'import "./util.js"', P("util.js"): b"export {}"}
    skipped = {}
    T.hash_names(site, skipped=skipped)
    assert P("util.js") in skipped
    assert "main.js" in skipped[P("util.js")]


def test_at_import_blocks_renaming_and_unquoted_urls_are_rewritten():
    site = {
        P("css/main.css"): b'@import "theme.css";\n.hero { background: url(../img/pic.png) }',
        P("css/theme.css"): b"body { color: red }",
        P("img/pic.png"): png((4, 4)),
    }
    skipped = {}
    renames = T.hash_names(site, skipped=skipped)
    pic = next(p for p in site if p.suffix == ".png")
    main = next(p for p in site if p.name.startswith("main.") and p.suffix == ".css")
    css = site[main].decode()
    assert renames[P("img/pic.png")] == pic
    assert f"url(../img/{pic.name})" in css
    # the imported file is referenced by another stylesheet, so it keeps its name and says why
    assert P("css/theme.css") in site
    assert '@import "theme.css"' in css
    assert "main.css" in skipped[P("css/theme.css")]


def test_srcset_and_quoted_css_urls_are_rewritten():
    site = {P("i.html"): b'<img srcset="img/a.png 1x, img/a.png 2x" src="img/a.png">',
            P("s.css"): b'a{background:url("img/a.png")}', P("img/a.png"): png((4, 4))}
    T.hash_names(site)
    html, css = site[P("i.html")].decode(), next(v for k, v in site.items() if k.suffix == ".css").decode()
    assert html.count("img/a.") == 3 and "img/a.png" not in html
    assert "img/a." in css and "img/a.png" not in css


def test_absolute_url_reference_keeps_original_name_and_reports_it():
    site = {P("i.html"): b'<img src="b.png"><meta content="https://x.com/b.png">', P("b.png"): png((2, 2))}
    skipped = {}
    T.hash_names(site, skipped=skipped)
    assert P("b.png") in site  # one copy, both refs keep working
    assert "absolute URL" in skipped[P("b.png")]


def test_runtime_assembled_path_still_blocks_hashing():
    site = {P("index.html"): b'<img src="img/a.png">', P("app.js"): b'src = `img/${name}.png`',
            P("img/a.png"): png((4, 4))}
    skipped = {}
    T.hash_names(site, skipped=skipped)
    assert P("img/a.png") in site and P("img/a.png") in skipped


def test_runtime_filename_prefix_blocks_hashing_without_blocking_siblings():
    """`"/img/runtime-" + n + ".png"` names runtime-1.png, but not every image in img/."""
    site = {
        P("index.html"): b'<img src="img/other.png">',
        P("js/app.js"): b'var n = 1; img.src = "/img/runtime-" + n + ".png";',
        P("img/runtime-1.png"): png((4, 4)),
        P("img/other.png"): png((4, 4)),
    }
    skipped = {}
    renames = T.hash_names(site, skipped=skipped)
    assert P("img/runtime-1.png") in site
    assert P("img/runtime-1.png") in skipped
    assert "assembled at runtime" in skipped[P("img/runtime-1.png")]
    assert P("img/other.png") in renames


def test_runtime_template_filename_prefix_sets_the_extension():
    site = {
        P("index.html"): b'<img src="img/other.png">',
        P("js/app.js"): b'const path = `/img/runtime-${n}.png`;',
        P("img/runtime-1.png"): png((4, 4)),
        P("img/runtime-1.jpg"): b"not a real jpeg but a hashable sibling",
        P("img/other.png"): png((4, 4)),
    }
    skipped = {}
    T.hash_names(site, skipped=skipped)
    assert P("img/runtime-1.png") in skipped
    assert ".png" in skipped[P("img/runtime-1.png")]
    assert P("img/runtime-1.jpg") not in skipped


def test_runtime_filename_prefix_blocks_webp_conversion():
    site = {
        P("js/app.js"): b'var n = 1; img.src = "img/runtime-" + n + ".png";',
        P("img/runtime-1.png"): banner_png(),
        P("img/other.png"): banner_png(),
    }
    skipped = {}
    moves = T.to_webp(site, skipped=skipped)
    assert P("img/runtime-1.png") in site and P("img/runtime-1.png") in skipped
    assert P("img/other.png") in moves
    assert "assembled at runtime" in skipped[P("img/runtime-1.png")]


def test_avif_does_not_overwrite_an_existing_avif():
    site = {P("i.html"): b'<img src="a.png">', P("a.png"): banner_png(), P("a.avif"): b"existing"}
    assert T.to_avif(site) == {}
    assert site[P("a.avif")] == b"existing"
