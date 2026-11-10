import textwrap
from pathlib import Path

import click

from webtim import __version__
from webtim.branding import BrandedCommand, show_banner
from webtim.build import BuildError, build


def _pct(part: int, whole: int) -> str:
    return f"{(1 - part / whole) * 100:.1f}%" if whole else "0.0%"


@click.command(cls=BrandedCommand)
@click.version_option(__version__, prog_name="webtim")
@click.argument("input", type=click.Path(exists=True))
@click.option("-o", "--output", "output_dir", default=None, type=click.Path(),
              help="Output directory (default: <input>_optimized)")
@click.option("--dry-run", is_flag=True, help="Preview sizes without writing")
@click.option("-v", "--verbose", is_flag=True, help="Per-file sizes")
@click.option("--webp", is_flag=True, help="Convert PNG/JPEG to WebP when smaller (needs Pillow)")
@click.option("--avif", is_flag=True,
              help="Convert PNG/JPEG to AVIF when smaller, with a <picture> fallback (needs Pillow with AVIF)")
@click.option("--inline-small", "inline", type=int, default=0, metavar="BYTES",
              help="Inline images up to BYTES as data: URIs (2048 is a sane start; 0 = off; negative is rejected)")
@click.option("--defer-scripts", "defer", is_flag=True, help="Add defer to external scripts on pages with no inline code")
@click.option("--hash", "hash_names", is_flag=True, help="Rename assets to name.<hash>.ext for forever-caching")
@click.option("--emit-config", type=click.Choice(["nginx", "caddy"]),
              help="Write a server snippet next to the output dir (not inside it, so it isn't served)")
@click.option("--report", "report_path", type=click.Path(),
              help="Write a machine-readable JSON build report to this path")
@click.option("--manifest", "manifest_path", type=click.Path(),
              help="Write a JSON manifest mapping original assets to their hashed names")
def main(input, output_dir, dry_run, verbose, webp, avif, inline, defer, hash_names, emit_config,
         report_path, manifest_path):
    show_banner()
    if webp and avif:
        raise click.UsageError("--avif and --webp cannot be combined: choose one image format")
    if inline < 0:
        raise click.UsageError("--inline-small must be zero or greater")
    if webp or avif:
        import importlib.util
        if importlib.util.find_spec("PIL") is None:
            raise click.UsageError("--webp/--avif need Pillow: pip install Pillow")
        if avif:
            from PIL import features
            if not features.check("avif"):
                raise click.UsageError("--avif needs a Pillow build with AVIF support; upgrade Pillow")
    src = Path(input).resolve()
    out = Path(output_dir).resolve() if output_dir else Path(str(src) + "_optimized")
    try:
        report = build(
            src, out, webp=webp, avif=avif, inline=inline, defer=defer, hash_names=hash_names,
            emit_config=emit_config, manifest_path=manifest_path, report_path=report_path,
            dry_run=dry_run,
            verbose=(lambda message: click.echo(message)) if verbose else None,
            warn=lambda message: click.secho(f"warning: {message}", fg="yellow", err=True),
        )
    except BuildError as exc:
        if report_path and Path(report_path).is_file():
            click.echo(f"  Report: {Path(report_path).resolve()} (failed build)", err=True)
        raise click.ClickException(str(exc)) from exc

    if report["status"] == "no_web_files":
        click.echo(f"No HTML/CSS/JS files found in {src}")
        if report.get("report"):
            click.echo(f"  Report: {report['report']}")
        return

    counts, sizes = report["counts"], report["sizes"]
    click.echo()
    click.secho("Totals:", bold=True)
    click.echo(f"  Input:   {counts['discovered']:>4d} files {sizes['original_bytes']:>9d} bytes")
    saved = _pct(sizes["output_bytes"], sizes["original_bytes"])
    click.echo(f"  Output:  {counts['output_files']:>4d} files {sizes['output_bytes']:>9d} bytes "
               f"({click.style(saved, fg='green' if sizes['output_bytes'] <= sizes['original_bytes'] else 'yellow')} saved)")
    click.echo(f"  Over the wire, gzip:   {sizes['gzip_wire_bytes']:>9d} bytes "
               f"({_pct(sizes['gzip_wire_bytes'], sizes['original_bytes'])} vs input)")
    click.echo(f"  Over the wire, brotli: {sizes['brotli_wire_bytes']:>9d} bytes "
               f"({_pct(sizes['brotli_wire_bytes'], sizes['original_bytes'])} vs input)")
    click.echo(f"  Processed: {counts['processed']}  skipped: {counts['skipped']}  failed: {counts['failed']}")
    transforms = report["transformations"]
    changed = [f"{key} {value}" for key, value in transforms.items() if value]
    click.echo("  Transformations:")
    for line in textwrap.wrap(", ".join(changed) or "none", width=74):
        click.echo(f"    {line}")
    click.echo(f"  Time: {report['duration_ms'] / 1000:.2f}s")
    if not dry_run:
        click.echo(f"  Output dir: {out}")
        if report.get("config"):
            click.echo(f"  Server config: {report['config']}")
    if report.get("manifest"):
        click.echo(f"  Manifest: {report['manifest']}")
    if report["warnings"]:
        click.echo(f"  Warnings: {len(report['warnings'])} (see above)", err=True)
    if report.get("report"):
        click.echo(f"  Report: {report['report']}")


if __name__ == "__main__":
    main()
