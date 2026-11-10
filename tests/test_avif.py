import re
from pathlib import PurePosixPath as P

import pytest
from click.testing import CliRunner
from PIL import features

from tests.conftest import banner_png, png
from webtim import transforms as T
from webtim.build import BuildError, build
from webtim.cli import main

pytestmark = pytest.mark.skipif(not features.check("avif"), reason="Pillow has no AVIF support")


def test_avif_adds_a_picture_source_and_keeps_the_fallback():
    site = {P("i.html"): b'<img class="x" src="banner.png" alt="b">', P("banner.png"): banner_png()}
    skipped = {}
    assert T.to_avif(site, skipped=skipped) == {P("banner.png"): P("banner.avif")}
    html = site[P("i.html")].decode()
    assert '<source srcset="banner.avif" type="image/avif">' in html
    assert 'src="banner.png"' in html  # fallback source stays
    assert P("banner.png") in site  # original is never dropped
    assert len(site[P("banner.avif")]) < len(site[P("banner.png")])


def test_avif_skips_images_without_a_picture_slot():
    css_only = {P("s.css"): b"a{background:url(b.png)}", P("b.png"): banner_png()}
    skipped = {}
    assert T.to_avif(css_only, skipped=skipped) == {}
    assert P("b.png") in skipped and "<picture>" in skipped[P("b.png")]

    srcset = {P("i.html"): b'<img srcset="banner.png 1x, other.png 2x" src="banner.png">',
              P("banner.png"): banner_png(), P("other.png"): banner_png()}
    assert T.to_avif(srcset) == {}


def test_avif_leaves_images_alone_when_conversion_is_not_smaller():
    site = {P("i.html"): b'<img src="t.png">', P("t.png"): png((2, 2))}
    assert T.to_avif(site) == {}
    assert site[P("i.html")] == b'<img src="t.png">'


def test_avif_does_not_nest_picture_inside_picture():
    html = b'<picture><source srcset="b.webp"><img src="banner.png"></picture>'
    site = {P("i.html"): html, P("banner.png"): banner_png()}
    assert T.to_avif(site) == {}
    assert site[P("i.html")].lower().count(b"<picture>") == 1


def test_avif_and_hashing_rewrite_both_sources(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text('<img src="pic.png" alt="p">')
    (site / "pic.png").write_bytes(banner_png())
    out = tmp_path / "out"
    report = build(site, out, avif=True, hash_names=True)
    assert report["transformations"]["avif"] == 1
    names = sorted(p.name for p in out.iterdir())
    assert any(re.fullmatch(r"pic\.[0-9a-f]{8}\.avif", n) for n in names)
    assert any(re.fullmatch(r"pic\.[0-9a-f]{8}\.png", n) for n in names)
    index = (out / "index.html").read_text()
    # htmlmin strips optional attribute quotes, so accept both spellings
    assert re.search(r'<source srcset="?pic\.[0-9a-f]{8}\.avif"? type="?image/avif"?>', index)
    assert re.search(r'src="?pic\.[0-9a-f]{8}\.png"?', index)


def test_avif_cli_end_to_end(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text('<html><body><img src="pic.png" alt="p"></body></html>')
    (site / "pic.png").write_bytes(banner_png())
    out = tmp_path / "out"
    result = CliRunner().invoke(main, [str(site), "-o", str(out), "--avif"])
    assert result.exit_code == 0, result.output
    index = (out / "index.html").read_text()
    assert "image/avif" in index and "pic.avif" in index
    assert re.search(r'src="?pic\.png"?', index)  # fallback reference kept
    assert (out / "pic.png").is_file() and (out / "pic.avif").is_file()


def test_webp_and_avif_cannot_be_combined(site_dir, tmp_path):
    with pytest.raises(BuildError, match="cannot be combined"):
        build(site_dir, tmp_path / "out", webp=True, avif=True)
    result = CliRunner().invoke(main, [str(site_dir), "--webp", "--avif", "--dry-run"])
    assert result.exit_code != 0 and "cannot be combined" in result.output
