# Changelog

## Unreleased

### Changed

- **Terminal identity**: `webtim` and `webtim-serve` now show a compact three-line ASCII mark in
  `--help` and once at the start of an interactive run. The mark is not printed in redirected
  output, `--version`, reports or manifests. The build summary wraps only non-zero transformation
  counts at 74 columns; compression behavior, options and output file shapes are unchanged.
- No new runtime dependency was added. Packaging now requires `setuptools>=77` so the
  project can use PEP 639 license metadata.

### Added

- **Licensing**: MIT `LICENSE`, PEP 639 metadata and an explicit `license-files` entry. The
  wheel and sdist include the license.
- CLI identity regression coverage: compact/ASCII help mark and no banner in redirected build
  output. The suite is now **141 tests**.

### Fixed

- **Runtime filename prefixes**: `--hash` and `--webp` renamed `/img/runtime-1.png` when JavaScript
  built it as `"/img/runtime-" + n + ".png"`, leaving the page with a 404. Runtime detection now
  captures the literal filename prefix as well as directory prefixes, without blocking unrelated
  sibling assets. Regression coverage includes hashing, template literals and WebP.
- **Defer filename false positives**: `--defer-scripts` treated `defer-dep.js` / `async-use.js` in a
  `src` value as an existing `defer`/`async` attribute and skipped the page. Attribute detection now
  requires an actual attribute token; module detection is similarly token-based.
- **Negative inline threshold**: `--inline-small -1` now fails with a clear usage error instead of
  silently acting like zero; the `build()` API rejects it too.
- **EXIF orientation and ICC profiles**: WebP/AVIF conversion applied the raw pixel matrix, so a
  rotated JPEG/PNG displayed sideways after conversion and lost its embedded colour profile.
  Conversion now applies EXIF orientation before encoding and carries the ICC profile through;
  regression tests cover both formats.
- **Identity rejection**: `Accept-Encoding: identity;q=0` (or `*;q=0`) was ignored and the server
  sent an identity response. It now returns **406 Not Acceptable** when no compression is available
  and identity is explicitly excluded.
- **Animated PNG flattening**: `--webp` converted an APNG to a single-frame WebP, silently dropping
  animation. Animated images are now skipped and reported; `--avif` already skipped them but now
  records the same explicit reason.

### Validated

- Headed Chrome 157.0.8103.39 loaded all six fixture routes for eight build variants (48 page loads)
  with zero exceptions, failed requests or broken images. The browser validation ran on 139 tests;
  the current suite has **141 tests** after the CLI identity checks.
- Manifests and reports were checked against real output bytes; repeated full-option builds produced
  an identical tree and byte-identical manifest.
- See `docs/BROWSER-VALIDATION.md` and `evidence/` for the fixture, measurements, screenshots
  and limitations.

## 0.3.0

### Fixed

- **Content negotiation**: the development server parsed `Accept-Encoding` by substring, so
  `gzip;q=0` still served gzip, `br;q=0, gzip;q=1` served Brotli, and `*` was ignored. Quality
  values, explicit exclusions, wildcards and deterministic ties are now honoured.
- **WebP no longer overwrites an existing `.webp`**, and two sources cannot claim one output name.
- **Percent-encoded references** (`img/my%20file.png`) survive hashing and are inlined correctly.
- **Hash collisions**: hashing never renames onto an existing file; the skip is reported.
- **Silent skips**: code-referenced CSS/JS that cannot be renamed is recorded with its referrer in
  the manifest, report and warnings.
- **Symlinks** resolving outside the input, or broken, are skipped with a warning instead of shipped.
- **Build transaction**: the previous output is kept under a unique backup name (a pre-existing
  backup is never deleted), promotion failure restores it, a failed config/manifest/report install
  rolls the output back, and all three files are written atomically.
- **Report on failure**: a requested `--report` is still written with `status: "failed"` and the
  error message; a failed build leaves the previous manifest untouched.
- **Destination validation**: report, manifest and server config may not sit inside the input or
  output, may not be directories, and may not collide with each other.
- **Server-config injection**: generated Nginx/Caddy roots are quoted and escaped; line breaks
  and NUL bytes are rejected, so an output directory name cannot add a directive.
- **Image decompression bombs**: WebP/AVIF conversion skips images above 40,000,000 pixels before
  decoding instead of allocating unbounded memory.
- **Python 3.13**: `htmlmin` imports the removed stdlib `cgi`; a minimal stand-in is installed only
  when that import fails (htmlmin never calls it).

### Added

- `--avif`: opt-in AVIF with `<picture>` fallback, preserving the original; measured lossless AVIF
  was 84.8% smaller than the source PNG in one fixture, versus 36.2% for lossless WebP.
- `--manifest`: deterministic, versioned asset manifest (see `docs/asset-manifest.md`).
- Report diagnostics: largest files, largest measured compression savings, unrewritten assets and
  transformations that increased size (see `docs/build-report.md`).
- CI workflow: Python 3.10–3.13 on Ubuntu and Windows.
- 114 → 130 tests covering negotiation, manifest, AVIF, reference rewriting, minifier failures,
  symlinks, promotion rollback, report/manifest path safety, config quoting and image-size limits.
  (The browser-validation milestone added five more, and image-orientation coverage added two;
  the total was 138 before the animated-PNG regression; that 0.3.0 checkpoint total was 139.
  The Unreleased CLI terminal-identity tests raise the current baseline to 141.)

### Changed

- Pipeline output is byte-identical to 0.2.0 for default options and for
  `--hash --webp --inline-small 2048 --defer-scripts` (verified by comparing builds of the same
  fixture).
- Version 0.3.0.

### Not verified in this environment

- No Python 3.13 interpreter, nginx or Caddy binary is available; the 3.13 path is exercised by
  blocking the `cgi` import, and the CI matrix/real-server checks have not been run here.
