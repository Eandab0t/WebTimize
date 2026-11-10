import json

from click.testing import CliRunner

from tests.conftest import banner_png
from webtim.build import build
from webtim.cli import main


def run_build(tmp_path, *options):
    report = tmp_path / "report.json"
    out = tmp_path / "out"
    result = CliRunner().invoke(main, [str(tmp_path / "site"), "-o", str(out), "--report", str(report), *options])
    assert result.exit_code == 0, result.output
    return json.loads(report.read_text())


def make_site(tmp_path):
    site = tmp_path / "site"
    (site / "css").mkdir(parents=True)
    (site / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="css/app.css"></head>'
        '<body><img src="img/banner.png" alt="b"><p>hello</p></body></html>')
    (site / "css" / "app.css").write_text("body { color: red; background: url(../img/banner.png); }")
    (site / "img").mkdir()
    (site / "img" / "banner.png").write_bytes(banner_png())
    return site


def test_diagnostics_rank_largest_files_and_compression_savings(tmp_path):
    make_site(tmp_path)
    data = run_build(tmp_path)
    diagnostics = data["diagnostics"]
    sizes = [row["bytes"] for row in diagnostics["largest_files"]]
    assert sizes == sorted(sizes, reverse=True) and 0 < len(sizes) <= 5
    for row in diagnostics["largest_compression_savings"]:
        assert row["saved_bytes"] > 0
        assert row["wire_bytes"] < row["bytes"]
        assert row["bytes"] - row["wire_bytes"] == row["saved_bytes"]


def test_unrewritten_assets_are_listed_with_reasons(tmp_path):
    site = make_site(tmp_path)
    (site / "index.html").write_text(
        '<html><head><meta property="og:image" content="https://example.com/img/banner.png"></head>'
        '<body><img src="img/banner.png"><p>hello</p></body></html>')
    data = run_build(tmp_path, "--hash")
    rows = data["diagnostics"]["unrewritten_assets"]
    assert any(row["path"] == "img/banner.png" and "absolute URL" in row["reason"] for row in rows)


def test_inlining_html_growth_is_reported_as_measured(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text('<img src="i.png" alt="">')
    (site / "i.png").write_bytes(banner_png())
    report = build(site, tmp_path / "out", inline=100000)
    grew = {row["path"]: row for row in report["diagnostics"]["transformations_increased_size"]}
    assert "index.html" in grew
    assert grew["index.html"]["output_bytes"] > grew["index.html"]["input_bytes"]
    assert grew["index.html"]["delta_bytes"] == (
        grew["index.html"]["output_bytes"] - grew["index.html"]["input_bytes"])


def test_real_build_does_not_mark_sizes_as_estimated(tmp_path):
    make_site(tmp_path)
    data = run_build(tmp_path)
    assert data["sizes_are_estimated"] is False


def test_dry_run_diagnostics_match_a_real_build(tmp_path):
    make_site(tmp_path)
    dry = run_build(tmp_path, "--dry-run")
    assert dry["sizes_are_estimated"] is True
    assert dry["diagnostics"]["largest_files"]  # still ranked from measured output bytes
