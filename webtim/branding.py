import click

from webtim import __version__

BANNER = (
    "  +--+     +--+\n"
    f"  |  |     |  |  WEBTIMIZE COMPRESSOR {__version__}\n"
    "  +--+-----+--+  local-first build optimizer"
)


class BrandedCommand(click.Command):
    def format_help(self, ctx, formatter):
        formatter.write_paragraph()
        formatter.write(BANNER)
        formatter.write("\n\n")
        super().format_help(ctx, formatter)


def show_banner():
    """Print once on an interactive terminal; redirected output stays machine-readable."""
    if click.get_text_stream("stdout").isatty():
        click.echo()
        click.secho(BANNER, bold=True)
        click.echo()
