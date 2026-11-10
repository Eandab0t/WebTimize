import hashlib
import json

import pytest
from click.testing import CliRunner

from webtim.build import BuildError, build
from webtim.cli import main


def run(args):
    result = CliRunner().invoke(main, [str(a) for a in args])
    assert result.exit_code == 0, result.output
    return result


def test_manifest_maps_hashed_assets_and_records_skips(site_dir, tmp_path):
    manifest, out = tmp_path / "assets.json", tmp_path / "out"
    run([site_dir, "-o", out, "--hash", "--manifest", manifest])
    data = json.loads(manifest.read_text())
    assert data["schema_version"] == 1
    assert data["hash_algorithm"] == "sha256" and data["hash_length"] == 8
    by_original = {entry["original"]: entry for entry in data["assets"]}
    css = by_original["css/app.css"]
    assert css["hashed"] is True and css["final"].startswith("css/app.")
    assert css["type"] == "text/css" and css["reason"] is None
    # banner.png is named by an absolute og:image URL: kept under its original name, reason recorded
    banner = by_original["img/banner.png"]
    assert banner["hashed"] is False and banner["final"] == "img/banner.png"
    assert "absolute URL" in banner["reason"]
    for entry in data["assets"]:
        if entry["hashed"]:
            shipped = (out / entry["final"]).read_bytes()
            assert hashlib.sha256(shipped).hexdigest() == entry["sha256"]


def test_manifest_is_deterministic(site_dir, tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    run([site_dir, "-o", tmp_path / "out1", "--hash", "--manifest", first])
    run([site_dir, "-o", tmp_path / "out2", "--hash", "--manifest", second])
    assert first.read_bytes() == second.read_bytes()


def test_manifest_entries_are_sorted_by_original_path(site_dir, tmp_path):
    manifest = tmp_path / "assets.json"
    run([site_dir, "-o", tmp_path / "out", "--hash", "--manifest", manifest])
    originals = [entry["original"] for entry in json.loads(manifest.read_text())["assets"]]
    assert originals == sorted(originals)


def test_manifest_without_hashing_lists_every_asset_as_unhashed(tmp_path):
    site = tmp_path / "site"
    (site / "css").mkdir(parents=True)
    (site / "index.html").write_text('<link href="css/a.css" rel="stylesheet">')
    (site / "css" / "a.css").write_text("body{color:red}")
    manifest = tmp_path / "assets.json"
    run([site, "-o", tmp_path / "out", "--manifest", manifest])
    entries = json.loads(manifest.read_text())["assets"]
    assert entries and all(entry["hashed"] is False for entry in entries)
    assert all(entry["final"] == entry["original"] for entry in entries)
    assert all(entry["reason"] == "hashing not enabled" for entry in entries)


def test_manifest_is_written_for_dry_run_but_no_output(site_dir, tmp_path):
    manifest, out = tmp_path / "assets.json", tmp_path / "out"
    run([site_dir, "-o", out, "--hash", "--dry-run", "--manifest", manifest])
    assert not out.exists()
    data = json.loads(manifest.read_text())
    assert data["dry_run"] is True and data["assets"]


def test_manifest_inside_the_output_directory_is_rejected(site_dir, tmp_path):
    with pytest.raises(BuildError, match="inside the output"):
        build(site_dir, tmp_path / "out", manifest_path=tmp_path / "out" / "manifest.json")


def test_manifest_reports_the_hash_skipped_reason_in_the_report(site_dir, tmp_path):
    manifest, report = tmp_path / "m.json", tmp_path / "r.json"
    run([site_dir, "-o", tmp_path / "out", "--hash", "--manifest", manifest, "--report", report])
    data = json.loads(report.read_text())
    assert data["manifest"] == str(manifest)
    assert data["transformations"]["hashed_skipped"] == len(data["diagnostics"]["unrewritten_assets"])
