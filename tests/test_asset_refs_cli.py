"""End-to-end guard for rewritten references: the CLI output must load every hashed asset.

A browser reads an attribute as a URL, so a name with `%`, `#`, a space or non-ASCII has to be
written percent-encoded; the manifest keeps the raw filesystem path. This drives the public CLI,
not the transform, so a regression in the pipeline (or a later transform that re-rewrites refs)
fails here.
"""
import json
import re
from urllib.parse import unquote, urlsplit

import pytest
from click.testing import CliRunner
from PIL import features

from tests.conftest import banner_png
from webtim.cli import main

NAMES = [
    "img/nested/deep/icon.png",
    "img/with space.png",
    "img/pct%2ename.png",
    "img/pic#1.png",
    "img/logo-\u00fc.png",
]
REF = re.compile(r'(?:src|href)=(?:"([^"]*)"|([^\s>]+))')
SRCSET = re.compile(r'srcset=(?:"([^"]*)"|([^\s>]+))')


def _url_path(ref):
    """The file an attribute names, resolved the way a browser resolves the URL."""
    return unquote(urlsplit(ref).path).lstrip("/")


def _srcset_urls(value):
    """URLs from a srcset value: comma-separated candidates, `640w`/`2x` descriptors dropped."""
    urls = []
    for candidate in value.split(","):
        tokens = candidate.split()
        if tokens:
            urls.append(tokens[0])
    return urls


def _site(root):
    """A scratch site outside every repo: five asset-name shapes plus an ordinary CSS/JS pair."""
    site = root / "site"
    for name in NAMES:
        path = site / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(banner_png())
    (site / "css").mkdir(parents=True, exist_ok=True)
    (site / "js").mkdir()
    (site / "css" / "app.css").write_text("body { color: #123456; }  /* padding padding */\n")
    (site / "js" / "main.js").write_text('const pad = "padding padding";\n')
    (site / "index.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Refs</title>'
        '<link rel="stylesheet" href="css/app.css"></head><body>'
        + "".join(f'<img src="{name}" alt="">' for name in NAMES)
        + '<script src="js/main.js"></script></body></html>\n',
        encoding="utf-8",
    )
    return site


@pytest.mark.parametrize("flags", [[], ["--webp"]], ids=["hash", "hash-webp"])
def test_cli_rewrites_references_that_resolve_as_urls_to_the_files_written(tmp_path, flags):
    site = _site(tmp_path)
    out, manifest_path = tmp_path / "out", tmp_path / "manifest.json"
    result = CliRunner().invoke(
        main, [str(site), "-o", str(out), "--hash", "--manifest", str(manifest_path)] + flags
    )
    assert result.exit_code == 0, result.output

    html = (out / "index.html").read_text(encoding="utf-8")
    refs = [m.group(1) or m.group(2) for m in REF.finditer(html)]
    assert len(refs) == len(NAMES) + 2  # five images + stylesheet + script, nothing external
    for ref in refs:
        assert (out / _url_path(ref)).is_file(), f"{ref} does not name a written file"

    # Ordinary names are written exactly as before: no encoding at all.
    plain = next(ref for ref in refs if "nested/deep/icon" in ref)
    css, js = next(r for r in refs if r.startswith("css/")), next(r for r in refs if r.startswith("js/"))
    assert "%" not in plain and " " not in plain
    assert re.fullmatch(r"css/app\.[0-9a-f]{8}\.css", css)
    assert re.fullmatch(r"js/main\.[0-9a-f]{8}\.js", js)

    # The awkward shapes are encoded, and only in the way a browser needs.
    awkward = [ref for ref in refs if ref not in (plain, css, js)]
    assert len(awkward) == 4
    assert any("%20" in ref for ref in awkward)
    assert any("%252e" in ref for ref in awkward)
    assert any("%23" in ref for ref in awkward)
    assert any("%C3%BC" in ref for ref in awkward)

    # The manifest contract is untouched: `final` is the raw path of a file on disk, not a URL.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    finals = [asset["final"] for asset in manifest["assets"]]
    assert len(finals) == len(NAMES) + 2
    for final in finals:
        assert (out / final).is_file(), final
        assert "%20" not in final and "%23" not in final and "%252e" not in final
    assert any("with space." in final for final in finals)
    assert any("pct%2ename." in final for final in finals)
    assert any("pic#1." in final for final in finals)
    assert any("logo-\u00fc." in final for final in finals)


@pytest.mark.skipif(not features.check("avif"), reason="Pillow build without AVIF support")
def test_cli_avif_picture_sources_resolve_as_urls_to_the_files_written(tmp_path):
    """`to_avif` writes <source srcset> names itself; they must name the AVIFs actually written."""
    site = _site(tmp_path)
    out, manifest_path = tmp_path / "out", tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    result = CliRunner().invoke(
        main,
        [str(site), "-o", str(out), "--hash", "--avif", "--manifest", str(manifest_path),
         "--report", str(report_path)],
    )
    assert result.exit_code == 0, result.output

    html = (out / "index.html").read_text(encoding="utf-8")
    refs = [m.group(1) or m.group(2) for m in REF.finditer(html)]
    assert len(refs) == len(NAMES) + 2
    for ref in refs:
        assert (out / _url_path(ref)).is_file(), f"{ref} does not name a written file"

    srcsets = [m.group(1) or m.group(2) for m in SRCSET.finditer(html)]
    assert len(srcsets) == 3  # <picture> fallbacks: plain, space and non-ASCII images
    candidates = [url for value in srcsets for url in _srcset_urls(value)]
    assert len(candidates) == len(srcsets)
    for url in candidates:
        assert url.endswith(".avif")
        assert (out / _url_path(url)).is_file(), f"srcset {url} does not name a written file"
    plain = next(url for url in candidates if "nested/deep/icon" in url)
    assert "%" not in plain and " " not in plain  # an ordinary name is not encoded
    assert any("%20" in url for url in candidates)
    assert any("%C3%BC" in url for url in candidates)

    # An image that gets no <picture> source is never dropped silently: it is counted as an AVIF
    # skip and named, with a reason, in the report the pipeline already surfaces.
    assert len([ref for ref in refs if ref.endswith(".png")]) == 5  # 3 of 5 got a <picture>
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["transformations"]["avif_skipped"] == 2
    for name, cause in (("img/pct%2ename.png", "percent-decoded"), ("img/pic#1.png", "fragment starts")):
        warning = next((w for w in report["warnings"] if w.startswith(f"{name}:")), None)
        assert warning, f"{name} has no recorded reason"
        assert "does not resolve as a URL" in warning and cause in warning, warning
        assert "only named where no <picture> fallback can be added" not in warning, warning
        assert any(warning in line for line in result.output.splitlines()), "reason not surfaced on the CLI"

    # Ordinary src/href stay byte-identical to the unencoded spelling, as without the transform.
    assert re.search(r'(?:src|href)="?img/nested/deep/icon\.[0-9a-f]{8}\.png', html)
    assert re.search(r'(?:src|href)="?css/app\.[0-9a-f]{8}\.css', html)
    assert re.search(r'(?:src|href)="?js/main\.[0-9a-f]{8}\.js', html)

    # The manifest is read as data only: `final` is still the raw path of a file on disk.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    finals = [asset["final"] for asset in manifest["assets"]]
    for final in finals:
        assert (out / final).is_file(), final
        assert "%20" not in final and "%23" not in final and "%252e" not in final
    assert any(final.startswith("img/with space.") for final in finals)
    assert any(final.startswith("img/pct%2ename.") for final in finals)
    assert any(final.startswith("img/pic#1.") for final in finals)
    assert any(final.startswith("img/logo-\u00fc.") for final in finals)
    assert any(final.endswith(".avif") for final in finals)
