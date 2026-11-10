# WebTimize Compressor terminal identity

**Date:** 2026-11-07

Compressor remains a CLI-first tool: the `webtim` build command and the `webtim-serve` static-file
server. No GUI, `.exe`, packaging change or new dependency was introduced.

## Mark

```text
  +--+     +--+
  |  |     |  |  WEBTIMIZE COMPRESSOR 0.3.0
  +--+-----+--+  local-first build optimizer
```

The three-line mark adapts the Live logo's two slabs, horizontal base and text label using ASCII
characters only. It is printed once:

- at the top of `webtim --help` and `webtim-serve --help`;
- at the start of a `webtim`/`webtim-serve` run when stdout is an interactive terminal.

It is not printed for `--version`, redirected/piped output, JSON reports or asset manifests. The
existing Click colour rules still apply: savings are green, warnings are yellow, ANSI is stripped
when output is not a terminal, and `NO_COLOR` yields plain text. Branding is never required for a
build to succeed.

## Summary presentation

The existing `Totals:` block and aligned numeric columns are retained. Transformation counts now
list only non-zero values and wrap at 74 columns rather than producing one very long line. This is
display-only; the build report, manifest and compression results are unchanged.

## Evidence

`evidence/cli-identity/` contains actual captures:

- `before-help.txt` — the pre-branding Click help, reproduced from the previous `cli.py`.
- `help.txt` — branded help (plain, because stdout was redirected).
- `help-no-color.txt` — branded help with `NO_COLOR=1`; no escape bytes.
- `terminal-run.txt` — the build summary as printed in an actual pseudo-terminal, with terminal
  control sequences stripped for readability.
- `redirected-run.txt` — the same command redirected; no banner, no ANSI, JSON report written.
- `report.json`, `manifest.json` — parsed structured outputs.
- `error.txt` — invalid input, exit code 2.
- `exit-codes.txt` — recorded exit statuses.

The fixture site and output are under ignored `__development/experiments/cli-identity/`; the curated capture set is
versioned in `evidence/`.

## Verification

```bash
py -3.12 -m pytest -q
py -3.12 -m webtim --help
py -3.12 -m webtim __development/experiments/cli-identity/site -o __development/experiments/cli-identity/out \
  --hash --report __development/experiments/cli-identity/report.json \
  --manifest __development/experiments/cli-identity/manifest.json
PYTHONPATH=. py -3.12 -c "import runpy; from click.testing import CliRunner; ns=runpy.run_path('__development/experiments/cli-identity/before-cli.py'); print(CliRunner().invoke(ns['main'], ['--help'], prog_name='webtim').output)"
```

The full suite passed **141 tests** under Python 3.12.10 with Pillow 12.2.0. The default `python`
interpreter on this machine is 3.11.9 and lacks Pillow; using it is an environment error, not a
project dependency change. No global Python installation was modified.
