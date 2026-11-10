# Compressor CLI identity evidence

Captured 2026-11-07 with Python 3.12.10 and the local `webtim` package.

| File | Capture |
| --- | --- |
| `before-help.txt` | Pre-branding Click help, run from the previous committed `cli.py` with `prog_name="webtim"`. |
| `help.txt` | Branded `webtim --help`, stdout redirected. |
| `help-no-color.txt` | Branded help with `NO_COLOR=1`; plain text. |
| `terminal-run.txt` | Build summary from an actual pseudo-terminal, terminal control sequences stripped. |
| `redirected-run.txt` | Same build with stdout redirected: no banner and no ANSI. |
| `report.json` | Machine-readable report from that build. |
| `manifest.json` | Machine-readable asset manifest from that build. |
| `error.txt` | Invalid `--inline-small -1`, exit code 2. |
| `exit-codes.txt` | Recorded success/error statuses. |

The source fixture and generated output live under the gitignored `__development/experiments/cli-identity/`
directory. The banner is intentionally absent from redirected output, reports and manifests.
