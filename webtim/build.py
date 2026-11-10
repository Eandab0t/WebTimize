"""Build a static site: discover, optimize, validate, and publish atomically."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import brotli

from webtim import __version__
from webtim.compress import compress
from webtim.css_minify import minify as minify_css
from webtim.html_minify import minify as minify_html
from webtim.js_minify import minify as minify_js
from webtim.serve_config import caddy, nginx
from webtim import transforms as T

MINIFIERS = {".html": minify_html, ".css": minify_css, ".js": minify_js}
COMPRESSIBLE = {".html", ".css", ".js", ".svg", ".json", ".xml", ".txt"}
SKIP_DIRS = {"node_modules"}
MANIFEST_SCHEMA = 1


class BuildError(RuntimeError):
    """A build that must stop without replacing the published output."""


def collect(input_path: Path, exclude: Path | None = None, refused: list | None = None) -> dict:
    """Read the input into {relative PurePosixPath: bytes}, skipping hidden dirs and `exclude`.

    A symlink that resolves outside the input directory (or is broken) is not followed: a build
    must not ship files from outside the tree the user pointed it at. Such entries are appended to
    `refused` as (path, reason) so the caller can warn about them.
    """
    if input_path.is_file():
        return {PurePosixPath(input_path.name): input_path.read_bytes()}
    root_resolved = input_path.resolve()

    def inside(path: Path) -> bool:
        try:
            return path.resolve().is_relative_to(root_resolved)
        except OSError:
            return False

    def allow(path: Path) -> bool:
        if not path.is_symlink():
            return True
        if not path.exists():
            if refused is not None:
                refused.append((str(path), "broken symlink, skipped"))
            return False
        if not inside(path):
            if refused is not None:
                refused.append((str(path), "symlink resolves outside the input directory, skipped"))
            return False
        return True

    files = {}
    for root, dirs, names in os.walk(input_path):
        dirs[:] = [
            d for d in sorted(dirs)
            if not d.startswith(".") and d not in SKIP_DIRS
            and (exclude is None or (Path(root) / d).resolve() != exclude)
            and allow(Path(root) / d)
        ]
        for name in sorted(names):
            path = Path(root) / name
            if not allow(path):
                continue
            files[PurePosixPath(path.relative_to(input_path).as_posix())] = path.read_bytes()
    return files


def optimize(
    files: dict,
    *,
    webp: bool = False,
    avif: bool = False,
    inline: int = 0,
    defer: bool = False,
    hash_names: bool = False,
    warn=lambda message: None,
    stats: dict | None = None,
) -> dict:
    """Run the pipeline on {path: bytes} and return the optimized {path: bytes}."""
    stats = stats if stats is not None else {}
    stats.setdefault("minified", [])
    stats.setdefault("minified_unchanged", [])
    stats.setdefault("skipped", [])
    stats.setdefault("failed", [])
    stats.setdefault("webp", {})
    stats.setdefault("avif", {})
    stats.setdefault("inlined", 0)
    stats.setdefault("dropped", [])
    stats.setdefault("hashed", {})
    stats.setdefault("hash_skipped", {})
    stats.setdefault("manifest", [])
    stats.setdefault("deferred", 0)

    site, undecodable = {}, {}
    for path, data in files.items():
        if path.suffix in MINIFIERS:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                warn(f"{path}: not UTF-8, copied unchanged")
                stats["skipped"].append(str(path))
                undecodable[path] = data
                continue
        site[path] = data

    def minify(path, minifier) -> None:
        before = site[path]
        try:
            site[path] = minifier(before.decode("utf-8")).encode("utf-8")
        except Exception as exc:  # a minifier choking must never lose the original file
            warn(f"{path}: minify failed ({exc}), copied unchanged")
            stats["failed"].append(str(path))
            return
        bucket = "minified" if site[path] != before else "minified_unchanged"
        stats[bucket].append(str(path))

    for path in [p for p in site if p.suffix in {".css", ".js"}]:
        minify(path, MINIFIERS[path.suffix])

    if webp and avif:
        raise BuildError("--webp and --avif cannot be combined: choose one image format")
    for enabled, name, skip_key in ((webp, "webp", "webp_skipped"), (avif, "avif", "avif_skipped")):
        if not enabled:
            continue
        skipped_images: dict = {}
        moves = T.to_webp(site, skipped=skipped_images) if name == "webp" else T.to_avif(site, skipped=skipped_images)
        stats[name] = {str(old): str(new) for old, new in moves.items()}
        stats[skip_key] = {str(path): reason for path, reason in skipped_images.items()}
        for reason in skipped_images.values():
            warn(reason)
    if inline:
        dropped = []
        stats["inlined"] = T.inline_small(site, inline, dropped=dropped)
        stats["dropped"].extend(str(path) for path in dropped)
    if hash_names:
        skipped_hash: dict = {}
        stats["hashed"] = {
            str(old): str(new) for old, new in T.hash_names(
                site, skipped=skipped_hash, manifest=stats["manifest"]
            ).items()
        }
        stats["hash_skipped"] = {str(path): reason for path, reason in skipped_hash.items()}
        for reason in skipped_hash.values():
            warn(reason)
    if defer:
        stats["deferred"] = T.defer_scripts(site)

    for path in [p for p in site if p.suffix == ".html"]:
        minify(path, minify_html)
    return {**site, **undecodable}


def _measure(site: dict) -> tuple[list[dict], dict]:
    """Return report records and the generated sidecar bytes for each output file."""
    records, payloads = [], {}
    for path, data in sorted(site.items()):
        gzip_bytes, brotli_bytes = b"", b""
        if path.suffix in COMPRESSIBLE:
            gzip_bytes, brotli_bytes = compress(data)
        if len(gzip_bytes) >= len(data):
            gzip_bytes = b""
        if len(brotli_bytes) >= len(data):
            brotli_bytes = b""
        payloads[path] = (gzip_bytes, brotli_bytes)
        records.append({
            "path": str(path),
            "bytes": len(data),
            "gzip_bytes": len(gzip_bytes),
            "brotli_bytes": len(brotli_bytes),
        })
    return records, payloads


def _write_site(site: dict, payloads: dict, destination: Path, verbose=None) -> None:
    for path, data in sorted(site.items()):
        gzip_bytes, brotli_bytes = payloads[path]
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if gzip_bytes:
            target.with_name(target.name + ".gz").write_bytes(gzip_bytes)
        if brotli_bytes:
            target.with_name(target.name + ".br").write_bytes(brotli_bytes)
        if verbose:
            verbose(
                f"  {path}  {len(data):>8d} B  "
                f"gz {len(gzip_bytes) or '-':>6}  br {len(brotli_bytes) or '-':>6}"
            )


def validate_sidecars(destination: Path, site: dict) -> list[str]:
    """Confirm every generated sidecar decompresses back to its source bytes."""
    errors = []
    for path, data in sorted(site.items()):
        target = destination / path
        for suffix, decompress in ((".gz", gzip.decompress), (".br", brotli.decompress)):
            sidecar = target.with_name(target.name + suffix)
            if not sidecar.exists():
                continue
            try:
                if decompress(sidecar.read_bytes()) != data:
                    errors.append(f"{path}{suffix}: content mismatch")
            except Exception as exc:
                errors.append(f"{path}{suffix}: cannot decompress ({exc})")
    return errors


def _inside(path: Path, root: Path) -> bool:
    """True when `path` is `root` or lives under it, after resolution."""
    try:
        return path == root or path.is_relative_to(root)
    except OSError:
        return False


def _guard_destination(label: str, target: Path, source: Path, output: Path) -> None:
    """Refuse paths that would modify the input, be deleted with the output, or clobber a directory."""
    if _inside(target, output):
        raise BuildError(f"{label} must not be inside the output directory: {target}")
    if _inside(target, source):
        raise BuildError(f"{label} must not be inside the input directory: {target}")
    if target.exists() and target.is_dir():
        raise BuildError(f"{label} exists and is a directory: {target}")
    if target.parent.exists() and not target.parent.is_dir():
        raise BuildError(f"{label} parent is not a directory: {target.parent}")


def _atomic_write(path: Path, text: str) -> None:
    """Write text so a reader sees either the old file or the complete new one, never a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _promote(staging: Path, output: Path) -> Path | None:
    """Move the staging build into place and return the previous output's new location.

    The backup name is unique to this build, so a leftover backup from an interrupted run (or a
    file the user keeps there) is never deleted. If the final rename fails, the previous output is
    restored before the error propagates.
    """
    backup = None
    if output.exists():
        backup = output.parent / f".{output.name}.backup-{uuid.uuid4().hex[:12]}"
        if backup.exists():  # cannot happen with a fresh UUID, but never overwrite on a bad day
            raise BuildError(f"backup path already exists: {backup}")
        try:
            output.rename(backup)
        except OSError as exc:
            raise BuildError(f"cannot move the previous output aside: {exc}") from exc
    try:
        staging.rename(output)
    except BaseException:
        if backup is not None:
            try:
                backup.rename(output)
            except OSError as exc:  # keep the data and say exactly where it is
                raise BuildError(f"build failed and the previous output is at {backup}: {exc}") from exc
        raise
    return backup


def _rollback(output: Path, backup: Path | None) -> None:
    """Put the previous output back after a later step of the transaction failed."""
    if backup is None:
        shutil.rmtree(output, ignore_errors=True)
        return
    replacement = output.parent / f".{output.name}.failed-{uuid.uuid4().hex[:12]}"
    try:
        output.rename(replacement)
        backup.rename(output)
    except OSError as exc:
        raise BuildError(f"could not restore the previous output; it is kept at {backup}: {exc}") from exc
    shutil.rmtree(replacement, ignore_errors=True)


def _pct(original: int, final: int) -> float:
    return round((1 - final / original) * 100, 1) if original else 0.0


def _provenance(files: dict, stats: dict) -> dict:
    """Map each input path to the output path that now carries its bytes."""
    final = {path: path for path in files}
    for key in ("webp", "avif", "hashed"):
        for old, new in stats.get(key, {}).items():
            old, new = PurePosixPath(old), PurePosixPath(new)
            for source, destination in list(final.items()):
                if destination == old:
                    final[source] = new
    return final


def _diagnostics(site: dict, records: list[dict], files: dict, stats: dict) -> dict:
    """Rankings and review items. Every number is measured from the build, never estimated."""
    largest = sorted(site.items(), key=lambda item: (-len(item[1]), str(item[0])))[:5]
    savings = []
    for record in records:
        wire = min(record["gzip_bytes"] or record["bytes"],
                   record["brotli_bytes"] or record["bytes"])
        if wire < record["bytes"]:
            savings.append({
                "path": record["path"],
                "bytes": record["bytes"],
                "wire_bytes": wire,
                "saved_bytes": record["bytes"] - wire,
            })
    savings.sort(key=lambda row: (-row["saved_bytes"], row["path"]))
    provenance = {str(destination): len(files[source]) for source, destination in _provenance(files, stats).items()}
    grew = []
    for path, data in site.items():
        before = provenance.get(str(path))
        if before is not None and len(data) > before:
            grew.append({
                "path": str(path),
                "input_bytes": before,
                "output_bytes": len(data),
                "delta_bytes": len(data) - before,
            })
    grew.sort(key=lambda row: (-row["delta_bytes"], row["path"]))
    return {
        "largest_files": [{"path": str(path), "bytes": len(data)} for path, data in largest],
        "largest_compression_savings": savings[:5],
        "unrewritten_assets": [
            {"path": path, "reason": reason}
            for path, reason in sorted(stats.get("hash_skipped", {}).items())
        ],
        "transformations_increased_size": grew,
    }


def _manifest(site: dict, stats: dict, hash_names: bool, dry_run: bool) -> dict:
    """The asset manifest: one deterministic entry per hashable asset, in original-path order."""
    entries = list(stats.get("manifest") or [])
    if not hash_names:
        entries = [
            {
                "original": str(path),
                "final": str(path),
                "sha256": hashlib.sha256(data).hexdigest(),
                "type": T.asset_type(path),
                "hashed": False,
                "reason": "hashing not enabled",
            }
            for path, data in sorted(site.items())
            if path.suffix.lower() in T.HASHABLE
        ]
    return {
        "schema_version": MANIFEST_SCHEMA,
        "tool": {"name": "webtim", "version": __version__},
        "hash_algorithm": "sha256",
        "hash_length": T.HASH_LEN,
        "dry_run": dry_run,
        "assets": entries,
    }


def build(
    input_path: Path | str,
    output_path: Path | str,
    *,
    webp: bool = False,
    avif: bool = False,
    inline: int = 0,
    defer: bool = False,
    hash_names: bool = False,
    emit_config: str | None = None,
    manifest_path: Path | str | None = None,
    report_path: Path | str | None = None,
    dry_run: bool = False,
    verbose=None,
    warn=lambda message: None,
) -> dict:
    """Build a site and return a machine-readable report.

    The transaction is: validate every destination, stage and validate the output, generate the
    server config, promote the staging directory (keeping the previous output under a unique
    backup name), then swap in the config, manifest and report. A failure after promotion rolls
    the previous output back; the requested report is still written with `status: "failed"`.
    """
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    source = Path(input_path).resolve()
    output = Path(output_path).resolve()
    warnings: list[str] = []

    def record_warning(message: str) -> None:
        if message not in warnings:
            warnings.append(message)
            warn(message)

    manifest_target = Path(manifest_path).resolve() if manifest_path else None
    report_target = Path(report_path).resolve() if report_path else None
    config_target = None
    if emit_config:
        config_target = (output.parent / f"{output.name}.{emit_config}.conf").resolve()
        _guard_destination("server config", config_target, source, output)
    if manifest_target is not None:
        _guard_destination("manifest", manifest_target, source, output)
    if report_target is not None:
        _guard_destination("report", report_target, source, output)
    destinations = [target for target in (manifest_target, report_target, config_target) if target]
    if len(set(destinations)) != len(destinations):
        raise BuildError("manifest, report and server config must be different files")

    report = {
        "schema_version": 1,
        "tool": {"name": "webtim", "version": __version__},
        "input": str(source),
        "output": str(output),
        "dry_run": dry_run,
        "sizes_are_estimated": dry_run,
        "started_at": started_at,
        "status": "ok",
        "counts": {},
        "sizes": {},
        "transformations": {},
        "files": [],
        "warnings": warnings,
        "errors": [],
    }

    def write_report() -> None:
        if report_target is None:
            return
        report["duration_ms"] = round((time.perf_counter() - started) * 1000)
        try:
            _atomic_write(report_target, json.dumps(report, indent=2) + "\n")
        except OSError as exc:
            raise BuildError(f"cannot write report {report_target}: {exc}") from exc
        report["report"] = str(report_target)

    try:
        if output == source or output in source.parents:
            raise BuildError("output must not be the input directory or contain it")
        if output.exists() and not output.is_dir():
            raise BuildError(f"output exists and is not a directory: {output}")
        if emit_config not in (None, "nginx", "caddy"):
            raise BuildError(f"unknown server config: {emit_config}")
        if webp and avif:
            raise BuildError("--webp and --avif cannot be combined: choose one image format")
        if inline < 0:
            raise BuildError("inline threshold must be zero or greater")
        refused: list = []
        files = collect(source, exclude=output, refused=refused)
        for path, reason in refused:
            record_warning(f"{path}: {reason}")
        if not any(path.suffix in MINIFIERS for path in files):
            report["status"] = "no_web_files"
            write_report()
            report["duration_ms"] = round((time.perf_counter() - started) * 1000)
            return report

        stats: dict = {}
        site = optimize(
            files, webp=webp, avif=avif, inline=inline, defer=defer, hash_names=hash_names,
            warn=record_warning, stats=stats,
        )
        records, payloads = _measure(site)
        report["files"] = records
        report["diagnostics"] = _diagnostics(site, records, files, stats)
        report["transformations"] = {
            "minified": len(stats["minified"]),
            "minified_unchanged": len(stats["minified_unchanged"]),
            "webp": len(stats["webp"]),
            "webp_skipped": len(stats.get("webp_skipped", {})),
            "avif": len(stats["avif"]),
            "avif_skipped": len(stats.get("avif_skipped", {})),
            "inlined": stats["inlined"],
            "hashed": len(stats["hashed"]),
            "hashed_skipped": len(stats["hash_skipped"]),
            "deferred": stats["deferred"],
            "dropped": len(stats["dropped"]),
        }
        original_bytes = sum(len(data) for data in files.values())
        output_bytes = sum(record["bytes"] for record in records)
        gzip_wire = sum(record["gzip_bytes"] or record["bytes"] for record in records)
        brotli_wire = sum(record["brotli_bytes"] or record["bytes"] for record in records)
        report["sizes"] = {
            "original_bytes": original_bytes,
            "output_bytes": output_bytes,
            "saved_bytes": original_bytes - output_bytes,
            "saved_percent": _pct(original_bytes, output_bytes),
            "gzip_wire_bytes": gzip_wire,
            "brotli_wire_bytes": brotli_wire,
        }
        report["counts"] = {
            "discovered": len(files),
            "processed": len(files) - len(stats["skipped"]),
            "skipped": len(stats["skipped"]),
            "failed": len(stats["failed"]),
            "output_files": len(site),
        }
        if stats["failed"]:
            report["status"] = "partial"

        manifest_text = None
        if manifest_target is not None:
            manifest = _manifest(site, stats, hash_names, dry_run)
            manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

        if dry_run:
            if manifest_text is not None:
                _atomic_write(manifest_target, manifest_text)
                report["manifest"] = str(manifest_target)
            write_report()
            report["duration_ms"] = round((time.perf_counter() - started) * 1000)
            return report

        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() and any(output.iterdir()):
            record_warning(f"{output} is not empty; it will be replaced after a successful build")

        # Generate the config before touching anything so a generator failure leaves no trace.
        config_text = (nginx if emit_config == "nginx" else caddy)(str(output)) if emit_config else None

        staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=str(output.parent)))
        config_temp = None
        backup = None
        try:
            _write_site(site, payloads, staging, verbose)
            errors = validate_sidecars(staging, site)
            if errors:
                raise BuildError("; ".join(errors))
            if config_text is not None:
                config_temp = output.parent / f".{output.name}.{emit_config}-{uuid.uuid4().hex[:8]}.conf.tmp"
                config_temp.write_text(config_text, encoding="utf-8")
            backup = _promote(staging, output)
            staging = None
            if config_temp is not None:
                try:
                    os.replace(config_temp, config_target)
                except OSError as exc:
                    raise BuildError(f"cannot install the server config: {exc}") from exc
                config_temp = None
                report["config"] = str(config_target)
            if manifest_text is not None:
                _atomic_write(manifest_target, manifest_text)
                report["manifest"] = str(manifest_target)
            write_report()
        except BaseException:
            if backup is not None:
                _rollback(output, backup)
                backup = None
            raise
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if config_temp is not None and config_temp.exists():
                config_temp.unlink(missing_ok=True)

        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
            if backup.exists():
                record_warning(f"previous output backup could not be removed: {backup}")
        report["duration_ms"] = round((time.perf_counter() - started) * 1000)
        return report
    except BaseException as exc:
        report["status"] = "failed"
        report["errors"] = [str(exc) or exc.__class__.__name__]
        report["duration_ms"] = round((time.perf_counter() - started) * 1000)
        if report_target is not None:
            try:
                _atomic_write(report_target, json.dumps(report, indent=2) + "\n")
            except OSError:
                pass  # the original failure matters more than the failure report
        if isinstance(exc, (BuildError, KeyboardInterrupt, SystemExit)):
            raise
        raise BuildError(str(exc) or exc.__class__.__name__) from exc
