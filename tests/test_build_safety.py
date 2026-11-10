import json
import os

import pytest
from click.testing import CliRunner

import webtim.build as build_module
from webtim.build import BuildError, build
from webtim.cli import main


def test_output_containing_the_input_is_rejected(site_dir, tmp_path):
    with pytest.raises(BuildError, match="must not be"):
        build(site_dir, site_dir.parent)


def test_early_validation_failures_still_write_the_requested_report(site_dir, tmp_path):
    target = tmp_path / "out"
    target.write_text("not a directory")
    report_file = tmp_path / "report.json"
    with pytest.raises(BuildError, match="not a directory"):
        build(site_dir, target, report_path=report_file)
    report = json.loads(report_file.read_text())
    assert report["status"] == "failed"
    assert "not a directory" in report["errors"][0]


def test_output_that_is_a_file_is_rejected(site_dir, tmp_path):
    target = tmp_path / "out"
    target.write_text("not a directory")
    with pytest.raises(BuildError, match="not a directory"):
        build(site_dir, target)


def test_nested_output_inside_input_is_excluded_on_every_build(site_dir):
    nested = site_dir / "a" / "b"
    build(site_dir, nested)
    build(site_dir, nested)
    assert (nested / "index.html").is_file()
    assert not (nested / "a").exists()  # never walks into the previous build


def test_failed_validation_keeps_output_and_leaves_no_staging(site_dir, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("old")
    monkeypatch.setattr("webtim.build.validate_sidecars", lambda destination, site: ["broken"])
    with pytest.raises(BuildError, match="broken"):
        build(site_dir, out)
    assert (out / "keep.txt").read_text() == "old"
    assert not list(tmp_path.glob(".out-*"))


def test_failed_config_generation_keeps_previous_output(site_dir, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("old")

    def explode(root):
        raise RuntimeError("no nginx here")

    monkeypatch.setattr("webtim.build.nginx", explode)
    with pytest.raises(BuildError, match="no nginx here"):
        build(site_dir, out, emit_config="nginx")
    assert (out / "keep.txt").read_text() == "old"
    assert not list(tmp_path.glob(".out.*.conf.tmp"))


def test_minifier_crash_copies_the_file_and_marks_the_build_partial(site_dir, tmp_path, monkeypatch):
    def explode(source):
        raise RuntimeError("minifier bug")

    monkeypatch.setitem(build_module.MINIFIERS, ".js", explode)
    report = build(site_dir, tmp_path / "out")
    js_files = [path for path in site_dir.rglob("*.js")]
    assert report["status"] == "partial"
    assert report["counts"]["failed"] == len(js_files)
    assert (tmp_path / "out" / "js" / "app.js").read_bytes() == (site_dir / "js" / "app.js").read_bytes()


def test_failed_promotion_keeps_the_old_output_and_cleans_staging(site_dir, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("old")

    def explode(staging, output):
        raise RuntimeError("disk full")

    monkeypatch.setattr("webtim.build._promote", explode)
    with pytest.raises(BuildError, match="disk full"):
        build(site_dir, out)
    assert (out / "keep.txt").read_text() == "old"
    assert not list(tmp_path.glob(".out-*"))


def test_failed_build_still_writes_a_failure_report(site_dir, tmp_path, monkeypatch):
    report_file = tmp_path / "report.json"
    monkeypatch.setattr("webtim.build.validate_sidecars", lambda destination, site: ["broken"])
    result = CliRunner().invoke(main, [str(site_dir), "-o", str(tmp_path / "out"), "--report", str(report_file)])
    assert result.exit_code != 0
    report = json.loads(report_file.read_text())
    assert report["status"] == "failed"
    assert any("broken" in error for error in report["errors"])
    assert report["tool"]["name"] == "webtim"
    assert not list(tmp_path.glob(f".{report_file.name}.*.tmp"))


def test_a_pre_existing_backup_name_is_never_deleted(site_dir, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("old")
    leftover = tmp_path / ".out.backup"
    leftover.write_text("user data that must survive")
    build(site_dir, out)
    assert leftover.read_text() == "user data that must survive"
    assert (out / "index.html").is_file()


def test_successful_builds_leave_no_backup_or_staging(site_dir, tmp_path):
    out = tmp_path / "out"
    build(site_dir, out)
    build(site_dir, out)
    assert not list(tmp_path.glob(".out.bak*"))
    assert not list(tmp_path.glob(".out-*"))
    assert not list(tmp_path.glob(".out.failed-*"))


def test_existing_config_directory_is_rejected_before_anything_changes(site_dir, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("old")
    (tmp_path / "out.nginx.conf").mkdir()
    with pytest.raises(BuildError, match="is a directory"):
        build(site_dir, out, emit_config="nginx")
    assert (out / "keep.txt").read_text() == "old"
    assert (tmp_path / "out.nginx.conf").is_dir()


def test_failed_config_install_restores_the_previous_output(site_dir, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("old")
    real_replace = os.replace

    def flaky(source, destination):
        if str(source).endswith(".conf.tmp"):
            raise OSError("config install failed")
        return real_replace(source, destination)

    monkeypatch.setattr("webtim.build.os.replace", flaky)
    with pytest.raises(BuildError, match="cannot install the server config"):
        build(site_dir, out, emit_config="caddy")
    assert (out / "keep.txt").read_text() == "old"
    assert not list(tmp_path.glob(".out-*")) and not list(tmp_path.glob(".out.*.conf.tmp"))


def test_a_failed_build_keeps_the_previous_manifest(site_dir, tmp_path, monkeypatch):
    manifest = tmp_path / "assets.json"
    manifest.write_text('{"schema_version": 1, "assets": []}\n')
    monkeypatch.setattr("webtim.build.validate_sidecars", lambda destination, site: ["broken"])
    with pytest.raises(BuildError):
        build(site_dir, tmp_path / "out", manifest_path=manifest)
    assert manifest.read_text() == '{"schema_version": 1, "assets": []}\n'


def test_report_inside_the_input_or_output_is_rejected(site_dir, tmp_path):
    with pytest.raises(BuildError, match="inside the input directory"):
        build(site_dir, tmp_path / "out", report_path=site_dir / "report.json")
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(BuildError, match="inside the output directory"):
        build(site_dir, out, report_path=out / "report.json")


def test_manifest_inside_the_input_is_rejected(site_dir, tmp_path):
    with pytest.raises(BuildError, match="inside the input directory"):
        build(site_dir, tmp_path / "out", manifest_path=site_dir / "assets.json")


def test_report_and_manifest_must_be_different_files(site_dir, tmp_path):
    same = tmp_path / "out.json"
    with pytest.raises(BuildError, match="must be different"):
        build(site_dir, tmp_path / "out", report_path=same, manifest_path=same)


def test_empty_input_reports_no_web_files_and_writes_nothing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    report = build(empty, tmp_path / "out")
    assert report["status"] == "no_web_files"
    assert not (tmp_path / "out").exists()


def test_symlink_outside_the_source_is_not_followed(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html>x</html>")
    outside = tmp_path / "outside.txt"
    outside.write_text("do not ship me")
    try:
        (site / "leak.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available to this user")
    warnings = []
    build(site, tmp_path / "out", warn=warnings.append)
    assert not (tmp_path / "out" / "leak.txt").exists()
    assert any("outside the input directory" in message for message in warnings)


def test_symlink_inside_the_source_is_copied(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html>x</html>")
    (site / "real.txt").write_text("inside")
    try:
        (site / "link.txt").symlink_to(site / "real.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available to this user")
    build(site, tmp_path / "out")
    assert (tmp_path / "out" / "link.txt").read_text() == "inside"


def test_broken_symlink_is_reported_and_skipped(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html>x</html>")
    try:
        (site / "broken.txt").symlink_to(tmp_path / "missing.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available to this user")
    warnings = []
    report = build(site, tmp_path / "out", warn=warnings.append)
    assert report["status"] == "ok"
    assert not (tmp_path / "out" / "broken.txt").exists()
    assert any("broken symlink" in message for message in warnings)
