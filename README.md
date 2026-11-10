# WebTimize Compressor

Local-first, build-time optimizer for static sites. It minifies HTML/CSS/JS, writes Brotli and gzip
sidecars, optionally converts/inlines images (WebP or AVIF), cache-busts assets, defers scripts, and
emits nginx/Caddy configuration plus a JSON build report and an asset manifest. The Python package
and both commands remain named `webtim`.

```bash
pip install -e ".[images]"          # Pillow only needed for --webp / --avif
webtim ./site -o ./dist -v          # minify + .gz/.br sidecars
webtim ./site -o ./dist --webp --inline-small 2048 --defer-scripts --hash --emit-config nginx
webtim ./site -o ./dist --avif --hash --manifest dist-assets.json
webtim ./site --dry-run             # sizes only, writes nothing
webtim ./site -o ./dist --report build-report.json
webtim --version
webtim-serve ./dist                 # small deployments; production should use nginx/Caddy
```

`python -m webtim` does the same thing as `webtim`.

## Terminal identity

`webtim --help` and an interactive run start with the same compact ASCII mark:

```text
  +--+     +--+
  |  |     |  |  WEBTIMIZE COMPRESSOR 0.3.0
  +--+-----+--+  local-first build optimizer
```

The mark is three lines of ASCII, printed once; `webtim-serve` uses it too. It is shown on an
interactive terminal and in `--help`, but not in redirected output, `--version`, JSON reports or
asset manifests. Existing colour output is unchanged: success savings are green and warnings are
yellow, Click strips ANSI when output is not a terminal, and `NO_COLOR` produces plain text.

The summary keeps its existing `Totals:` wording and now lists only non-zero transformations, wrapped
to 74 columns instead of one long line. No compression option, file handling, report shape or
machine-readable output changed for the branding.

The captures and exact before/after behavior are recorded in
[docs/CLI-IDENTITY.md](docs/CLI-IDENTITY.md).

## Part of the WebTimize suite

WebTimize Compressor makes a site faster **before** it is deployed. Its sibling,
[WebTimize Live](https://github.com/Eandab0t/WebTimize-Live), improves pages **after** deployment
with reversible, site-specific CSS. They share no runtime, no server and no account; installing one
is never required for the other. The build report and asset manifest are deliberately
machine-readable so deployment tooling can consume them without knowing about Live.

## What each option does (all off by default except minify + sidecars)

| Option | Effect | Skips / limits |
|---|---|---|
| *(default)* | Minify HTML/CSS/JS; write `.gz` and `.br` next to text files, only when smaller than the original | Non-UTF-8 files and minifier errors: file copied unchanged with a warning. JS containing a nested template literal is left unminified |
| `--webp` | PNG/JPEG to WebP when smaller (lossless for PNG, q80 for JPEG); rewrites references | No `<picture>` fallback; an asset that would overwrite an existing `.webp` is left alone and reported; animated images are left in their original format |
| `--avif` | PNG/JPEG to AVIF when smaller, only where an `<img src>` can carry a `<picture><source type="image/avif">` fallback; the original stays as the fallback | Images named only from CSS, `srcset` or a runtime path are not converted (no safe fallback); needs a Pillow build with AVIF; cannot be combined with `--webp` |
| `--inline-small BYTES` | Images up to BYTES become `data:` URIs in HTML/CSS (SVG is URL-encoded, the rest base64); the file is dropped if nothing else names it | Can increase total bytes; measure with `--report`. Hurts caching if the page is shared by many routes. Negative values are rejected |
| `--defer-scripts` | Adds `defer` to external classic scripts | Pages with inline classic scripts are skipped entirely; module/async/already-deferred scripts untouched |
| `--hash` | Renames assets to `name.<sha256[:8]>.ext`, rewrites references (including percent-encoded and `srcset` spellings), so they can be cached for a year | JS/CSS that another JS/CSS file names (e.g. `import "./util.js"`) is not renamed, and the skip is recorded with its referrer. References built at runtime are invisible. An asset still named by an absolute URL (og:image, manifest) is left unhashed and reported, instead of shipping a second copy |
| `--emit-config nginx\|caddy` | Writes `<out>.nginx.conf` / `<out>.caddy.conf` **next to** the output dir | `brotli_static` is emitted commented out: it needs the ngx_brotli module |
| `--report PATH` | Writes a machine-readable JSON build report, including diagnostics (largest files, compression savings, unrewritten assets, transformations that grew) | Written even for `--dry-run`; dry-run sizes are estimates |
| `--manifest PATH` | Writes a versioned JSON asset manifest: original path, final path, SHA-256, MIME type, hashed flag and skip reason for every hashable asset | Must not be inside the output directory; written for `--dry-run` too |

Pipeline order: minify CSS/JS, WebP **or** AVIF, inline, hash, defer, minify HTML, compress.
HTML is minified last because the rewriting regexes expect the author's markup.

## Output safety

- The input directory is never modified.
- A build writes to a unique temporary staging directory, validates sidecars by decompressing them
  back to the original bytes, and only then replaces the output directory.
- Promotion moves the previous output to a **unique** backup name (`.dist.backup-<id>`). A leftover
  backup from an interrupted run, or a file you keep under that name, is never deleted.
- If promotion fails, the previous output is moved back. If a later step (server config, manifest or
  report write) fails, the new output is rolled back to the previous one and the error is raised.
- The server config is generated before anything is touched and installed atomically after
  promotion; a config that cannot be installed rolls the output back.
- `--report` and `--manifest` are written atomically (temp file + replace). A failed build still
  writes the requested report with `"status": "failed"` so automation can see why; a failed build
  leaves any previous manifest untouched.
- `--report`, `--manifest` and the generated server config may not be placed inside the input or
  output directory, may not be directories themselves, and may not be the same path. This prevents
  a build from modifying its own input, writing into a directory promotion will replace, or
  clobbering an unrelated file by path collision.
- `--dry-run` writes no output (it may still write a requested `--report` or `--manifest`).
- Symlinks are never followed out of the source tree: a link that resolves outside it, or a broken
  link, is skipped with a warning instead of shipping content the user did not point at.
- A successful build replaces the output directory, so files from an earlier run are not left behind.

## Build report

`--report build-report.json` emits a versioned report (`schema_version: 1`; the `diagnostics` block
was added in 0.3.0 and is additive):

```json
{
  "schema_version": 1,
  "status": "ok",
  "counts": { "discovered": 12, "processed": 12, "skipped": 0, "failed": 0, "output_files": 13 },
  "sizes": {
    "original_bytes": 184320,
    "output_bytes": 141900,
    "saved_bytes": 42420,
    "saved_percent": 23.0,
    "gzip_wire_bytes": 39120,
    "brotli_wire_bytes": 34210
  },
  "transformations": {
    "minified": 8, "minified_unchanged": 2, "webp": 2, "webp_skipped": 0,
    "avif": 0, "avif_skipped": 0, "inlined": 1, "hashed": 6, "hashed_skipped": 1,
    "deferred": 3, "dropped": 1
  },
  "diagnostics": {
    "largest_files": [ { "path": "img/banner.png", "bytes": 69138 } ],
    "largest_compression_savings": [
      { "path": "js/app.js", "bytes": 4021, "wire_bytes": 1204, "saved_bytes": 2817 }
    ],
    "unrewritten_assets": [
      { "path": "img/banner.png", "reason": "img/banner.png: absolute URL reference, not renamed" }
    ],
    "transformations_increased_size": [
      { "path": "index.html", "input_bytes": 412, "output_bytes": 2911, "delta_bytes": 2499 }
    ]
  },
  "duration_ms": 214
}
```

`status` is `ok`, `partial` (some transform failed and its file was copied unchanged), or
`no_web_files`. Per-file output records include `path`, `bytes`, `gzip_bytes`, and `brotli_bytes`.
`minified` counts every file whose bytes the minifier changed, HTML included; `minified_unchanged`
counts files the minifier ran on and returned identical, so the two together equal the number of
HTML/CSS/JS files processed — a build can no longer shrink 11 files while reporting 9.
`gzip_wire_bytes`/`brotli_wire_bytes` use the smaller generated sidecar (or the original when no
sidecar was written) and exclude HTTP overhead. For `--dry-run`, `sizes_are_estimated` is `true`.
`hashed_skipped` counts assets left unhashed because an absolute URL still names them or another
code file references them; each one also appears in `warnings`, so a build never hides why an asset
kept its original name. Diagnostics are measured from the build output, never estimated; the
`largest_compression_savings` rows report the smaller of the gzip/Brotli sidecars, so they never
overstate what a server can save.

## Asset manifest

`--manifest assets.json` (combine with `--hash` for renamed assets) writes a deterministic mapping
every pipeline can consume:

```json
{
  "schema_version": 1,
  "tool": { "name": "webtim", "version": "0.3.0" },
  "hash_algorithm": "sha256",
  "hash_length": 8,
  "dry_run": false,
  "assets": [
    { "original": "css/app.css", "final": "css/app.5bd292c2.css",
      "sha256": "5bd292c2…", "type": "text/css", "hashed": true, "reason": null },
    { "original": "img/banner.png", "final": "img/banner.png",
      "sha256": "…", "type": "image/png", "hashed": false,
      "reason": "img/banner.png: absolute URL reference, not renamed" }
  ]
}
```

Entries are sorted by `original`, `sha256` is the hash of the bytes that ship (for hashed files,
the bytes the filename encodes), and the file is byte-identical across repeated builds of the same
input. `schema_version` increments on breaking changes to the shape. Without `--hash` every entry is
listed with `hashed: false` and `reason: "hashing not enabled"`. See
[docs/asset-manifest.md](docs/asset-manifest.md) for details.

## Recommended builds

**Conservative:** `webtim ./site -o ./dist --hash --manifest assets.json --report report.json`.
This keeps image formats unchanged and adds safe cache-busting plus machine-readable outputs. Add
`--emit-config nginx|caddy` only after validating the snippet against the target server.

**Aggressive, measured on the browser fixture:**
`webtim ./site -o ./dist --webp --hash --inline-small 2048 --defer-scripts --manifest assets.json --report report.json`.
It reduced output by 11.9% and Brotli wire bytes by about 12.5% in that fixture. It has real
compatibility costs: WebP has no `<picture>` fallback, inlining can increase bytes, and deferred
scripts change execution timing. See [docs/BROWSER-VALIDATION.md](docs/BROWSER-VALIDATION.md) before
using it on a new site.

## Cache policy it generates

- Hashed files: `Cache-Control: public, max-age=31536000, immutable`
- Everything else (HTML included): `no-cache` (revalidate with ETag)

## Tests and browser validation

`pytest` (needs Pillow; node is used for JS round-trip tests when present). Current baseline:
**139 tests passing** (Python 3.12.10, Windows), including browser-found regressions for runtime
filename prefixes and `defer`/`async` filenames, plus EXIF-orientation, ICC-profile and animated-PNG
image tests. A real headed Chrome audit loaded 48 pages across
eight builds and six routes with zero exceptions, failed requests or broken images; see
[docs/BROWSER-VALIDATION.md](docs/BROWSER-VALIDATION.md) and the curated evidence in
[evidence/](evidence/).

The nginx output was checked against a real nginx 1.24 before the 2026-11-06 security-audit quoting
change; re-run `nginx -t` and the Caddy validator before deploying the current snippet. Brotli
serving and the Caddy snippet have **not** been run against real servers in this environment, and no
nginx/Caddy binary is installed here. CI (`.github/workflows/ci.yml`) runs the suite on Python
3.10–3.13 on Linux and Windows; those runs have not been executed locally. Python 3.13 is not
installed here, and Python 3.11 is present without the test dependencies.

## Layout

```text
webtim/            package: cli, branding, build orchestration, serve, serve_config, transforms, minifiers, compress
tests/             pytest suite
docs/              public documentation: CLI, reports, manifests and validation
evidence/          curated captures referenced by the docs
__development/     local-only experiments, private notes, builds and caches
SOURCES.md         why each default was chosen, and what was verified
.github/           CI matrix
```

## Known limitations

- Reference rewriting is regex-based; runtime-built URLs (`"img/" + name`) and every modern JS
  import form are not visible to it. Assets reached that way are deliberately not renamed.
- Hashing leaves an asset alone when an absolute URL names it, because the deploy host is unknown;
  those assets keep their original names and miss long-term caching. They are counted as
  `hashed_skipped` and listed as warnings.
- Inlining trades request count for bytes; it is not automatically a net win. On the browser fixture,
  256–2048 bytes dropped one request but increased output by 437 bytes, while 16 KiB/64 KiB grew it
  by 8%/43%.
- HTML minification uses the maintained `htmlmin2` distribution, which keeps the compatible
  `htmlmin.Minifier` API without depending on Python's removed `cgi` module.
- AVIF keeps the original as a fallback, so the output directory grows by the AVIF copy; browsers
  without AVIF fetch the original. CSS-, `srcset`- and JS-referenced images are not converted.
- Animated PNGs are not converted to WebP or AVIF: flattening them would discard frames. Animated
  GIFs are not image-converted either.
- Browser-level validation is a separate, local milestone, not an automatic CI gate; test
  representative sites before deploying transformed output.

Internal competitive research is kept in the local-only `__development/docs/` directory. See
[docs/asset-manifest.md](docs/asset-manifest.md) and [docs/build-report.md](docs/build-report.md) for
the machine-readable outputs.

## PyPI release

The repository contains a release-triggered publishing workflow using PyPI Trusted Publishing.
Before the first release, configure a PyPI trusted publisher for this repository and the `pypi`
GitHub environment. Then create and publish a GitHub Release whose tag matches the package version.
The workflow builds and validates the distribution before publishing it; no long-lived PyPI API
token is stored in GitHub.

## License

WebTimize Compressor is released under the MIT License. See [LICENSE](LICENSE).
Third-party dependencies retain their own licenses.
