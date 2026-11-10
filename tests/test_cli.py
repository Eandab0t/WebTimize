import gzip
import json
import re
import subprocess
import sys

import brotli
from click.testing import CliRunner

from webtim.cli import main


def run(*args):
    r = CliRunner().invoke(main, [str(a) for a in args])
    assert r.exit_code == 0, r.output
    return r.output


def test_default_run_keeps_tree_and_writes_sidecars(site_dir, tmp_path):
    out = tmp_path / "out"
    run(site_dir, "-o", out)
    # tree layout preserved (the old CLI flattened css/app.js and js/app.js onto each other)
    for rel in ["index.html", "docs/page.html", "css/app.css", "js/app.js", "img/icon.png", "img/banner.png"]:
        assert (out / rel).is_file(), rel
    html = (out / "index.html").read_text()
    assert "<!--" not in html
    assert gzip.decompress((out / "index.html.gz").read_bytes()).decode() == html
    assert brotli.decompress((out / "index.html.br").read_bytes()).decode() == html
    assert not (out / "img/icon.png.gz").exists()  # images are not recompressed


def test_dry_run_writes_nothing_and_does_not_crash(site_dir, tmp_path):
    out = tmp_path / "out"
    text = run(site_dir, "-o", out, "--dry-run", "-v")
    assert not out.exists()
    assert "Totals:" in text and "brotli" in text


def test_full_pipeline(site_dir, tmp_path):
    out = tmp_path / "out"
    run(site_dir, "-o", out, "--webp", "--inline-small", 2048, "--defer-scripts", "--hash", "--emit-config", "nginx")
    index = (out / "index.html").read_text()
    # icon: webp first (a 4x4 PNG shrinks), then inlined as that webp, and its file dropped
    assert "data:image/webp;base64," in index
    assert not list(out.glob("img/icon*"))
    # banner.png is named by an absolute og:image URL, so it is never converted or renamed: both
    # refs keep working and no second copy ships (that duplicate was the output-growth bug).
    assert not list(out.glob("img/banner.*.*"))
    assert (out / "img/banner.png").is_file()
    assert "/img/banner.png" in index and "https://example.com/img/banner.png" in index
    # hashed css/js referenced from html; app.js deferred, async other.js untouched
    app_js = next(p.name for p in (out / "js").glob("app.*.js"))
    assert re.search(rf'<script src="?js/{app_js}"? defer', index)
    assert not re.search(r"other\.[0-9a-f]{8}\.js[^>]*defer", index)
    # nested page: relative ref rewritten with ../ ; page has inline code, so it must NOT be deferred
    page = (out / "docs/page.html").read_text()
    assert f"../js/{app_js}" in page and "defer" not in page
    # css url(../img/icon.png) was converted and inlined too
    css = next((out / "css").glob("app.*.css")).read_text()
    assert "data:image/webp;base64," in css
    # config sits NEXT TO the output dir, never inside the served tree
    assert (tmp_path / "out.nginx.conf").is_file() and not list(out.glob("*.conf"))


def test_single_file_input(site_dir, tmp_path):
    out = tmp_path / "o"
    run(site_dir / "js" / "app.js", "-o", out)
    assert (out / "app.js").is_file()


def test_output_inside_input_is_not_reprocessed(site_dir):
    run(site_dir, "-o", site_dir / "dist")
    run(site_dir, "-o", site_dir / "dist")  # second run must not walk into dist/
    assert not (site_dir / "dist" / "dist").exists()


def test_undecodable_and_unminifiable_files_survive(site_dir, tmp_path):
    (site_dir / "js" / "latin.js").write_bytes("var s = 'caf\xe9';".encode("latin-1"))
    out = tmp_path / "out"
    run(site_dir, "-o", out)
    assert (out / "js/latin.js").read_bytes() == "var s = 'caf\xe9';".encode("latin-1")


def test_negative_inline_threshold_is_rejected(site_dir, tmp_path):
    r = CliRunner().invoke(main, [str(site_dir), "-o", str(tmp_path / "out"), "--inline-small", "-1"])
    assert r.exit_code != 0
    assert "zero or greater" in r.output
    assert not (tmp_path / "out").exists()


def test_webp_flag_without_pillow_is_a_clear_error(site_dir, monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    r = CliRunner().invoke(main, [str(site_dir), "--webp", "--dry-run"])
    assert r.exit_code != 0 and "Pillow" in r.output


def test_avif_flag_without_avif_support_is_a_clear_error(site_dir, monkeypatch):
    monkeypatch.setattr("PIL.features.check", lambda name: False)
    r = CliRunner().invoke(main, [str(site_dir), "--avif", "--dry-run"])
    assert r.exit_code != 0 and "AVIF" in r.output


def test_tiny_text_file_gets_no_sidecar(site_dir, tmp_path):
    """A sidecar larger than the file is never written (compression must not grow output)."""
    (site_dir / "tiny.txt").write_text("x")
    out = tmp_path / "out"
    run(site_dir, "-o", out)
    assert (out / "tiny.txt").read_text() == "x"
    assert not (out / "tiny.txt.gz").exists() and not (out / "tiny.txt.br").exists()


def test_no_web_files(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    assert "No HTML/CSS/JS" in run(tmp_path)


def test_json_report_records_counts_sizes_and_transformations(site_dir, tmp_path):
    report = tmp_path / "report.json"
    run(site_dir, "-o", tmp_path / "out", "--hash", "--defer-scripts", "--report", report)
    data = json.loads(report.read_text())
    assert data["status"] == "ok" and data["dry_run"] is False
    assert data["counts"]["discovered"] >= data["counts"]["skipped"]
    assert data["counts"]["output_files"] > 0
    assert data["sizes"]["saved_bytes"] == data["sizes"]["original_bytes"] - data["sizes"]["output_bytes"]
    assert data["transformations"]["hashed"] > 0 and data["transformations"]["deferred"] > 0
    assert data["duration_ms"] >= 0 and all("path" in f for f in data["files"])


def test_minify_counts_cover_html_and_report_idle_minifiers(site_dir, tmp_path):
    """`minified` counts every file minification changed, HTML included; idle runs are counted too.

    The old report counted only CSS/JS, so a build could shrink 11 files while reporting 9.
    """
    report = tmp_path / "report.json"
    run(site_dir, "-o", tmp_path / "out", "--report", report)
    data = json.loads(report.read_text())
    transforms = data["transformations"]
    targets = [p for p in site_dir.rglob("*") if p.suffix in {".html", ".css", ".js"}]
    assert transforms["minified"] + transforms["minified_unchanged"] == len(targets)
    assert transforms["minified"] >= 1 + 1  # index.html (has a comment) and app.css both shrink
    html = {f["path"] for f in data["files"] if f["path"].endswith(".html")}
    assert len(html) <= transforms["minified"] + transforms["minified_unchanged"]


def test_dry_run_report_writes_no_output(site_dir, tmp_path):
    out, report = tmp_path / "out", tmp_path / "report.json"
    run(site_dir, "-o", out, "--dry-run", "--report", report)
    assert not out.exists()
    data = json.loads(report.read_text())
    assert data["dry_run"] is True and data["sizes_are_estimated"] is True


def test_success_replaces_stale_output(site_dir, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "stale.txt").write_text("old")
    run(site_dir, "-o", out)
    assert not (out / "stale.txt").exists() and (out / "index.html").is_file()


def test_failed_validation_keeps_existing_output(site_dir, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("old")
    monkeypatch.setattr("webtim.build.validate_sidecars", lambda destination, site: ["broken"])
    r = CliRunner().invoke(main, [str(site_dir), "-o", str(out)])
    assert r.exit_code != 0 and "broken" in r.output
    assert (out / "keep.txt").read_text() == "old"


def test_output_cannot_replace_input(site_dir, tmp_path):
    before = (site_dir / "index.html").read_bytes()
    r = CliRunner().invoke(main, [str(site_dir), "-o", str(site_dir)])
    assert r.exit_code != 0 and "must not be" in r.output
    assert (site_dir / "index.html").read_bytes() == before


def test_identity_is_one_name():
    """The project answers to `webtim` everywhere: version text, help usage, package."""
    import webtim
    from webtim.serve import main as serve_main

    version = CliRunner().invoke(main, ["--version"]).output.strip()
    assert version == f"webtim, version {webtim.__version__}"

    usage = CliRunner().invoke(main, ["--help"], prog_name="webtim").output
    assert "Usage: webtim [OPTIONS] INPUT" in usage
    assert CliRunner().invoke(serve_main, ["--version"]).output.strip() == (
        f"webtim-serve, version {webtim.__version__}"
    )


def test_help_has_a_compact_ascii_mark():
    output = CliRunner().invoke(main, ["--help"], prog_name="webtim").output
    assert "WEBTIMIZE COMPRESSOR" in output
    assert "+--+-----+--+" in output
    assert all(ord(char) < 128 for char in output)
    assert len(output.splitlines()) < 40


def test_redirected_build_output_has_no_banner(site_dir, tmp_path):
    output = run(site_dir, "-o", tmp_path / "out", "--dry-run")
    assert "WEBTIMIZE COMPRESSOR" not in output
    assert "+--+" not in output

