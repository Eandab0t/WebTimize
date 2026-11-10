# Sources, tools and decisions

Everything used to build and verify this, and what each source was used for.
Retrieved 2026-10-28. Web sources informed design choices; behaviour claims about the code itself
were verified by running it (see "Verified here").

## Process
- **ponytail** (plugin skill): scope rule used throughout: smallest complete change, every transform opt-in, a test or self-check for each non-trivial piece, and a `shortcut: <limit>, <when to upgrade>` comment wherever a known limit remains (grep `shortcut:`).

## Software used (versions as installed in the build sandbox)
Python 3.12.3, click 8.3.1, htmlmin 0.1.12, cssmin 0.2.0, **rjsmin 1.3.0**, brotli 1.2.1, Pillow 12.1.1, pytest 9.2.0, Node v22.22.2 (JS round-trip tests), nginx 1.24.0 (config verification).
`jsmin` was removed, see below.

## What was wrong and what changed (found by reading and running the original code)
| Problem in 0.1.0 | Evidence | Fix |
|---|---|---|
| Three names for one project: the distribution, the command and the import package all differed | `pip show` reported 0.1.0, installed a differently named command and depended on `jsmin`, while the source was 0.2.0 | one name, `webtim`, for the distribution, package, both commands and the docs; version single-sourced from `webtim/__init__.py`; stale `*.egg-info` and the old editable install removed |
| `--dry-run` crashed | `gzip`/`brotli` imported inside `if out is not None` but used after it | imports at top, compress via `compress.py` |
| `html_minify.minify()` raised `TypeError` | `htmlmin.Minifier.minify()` has no `remove_optional_closing_tags` kwarg; probed directly | parameter removed |
| HTML comments never removed (its own test asserts they are) | probed: default `Minifier()` keeps them | `remove_comments=True` |
| Output flattened to one directory | `out / target.name` | relative paths kept |
| Non-web files not copied | only HTML/CSS/JS were written | all files copied through |
| `brotli` missing from dependencies | `pyproject.toml` | added |
| `tests/test_cli.py` imported `process_file` and `bench`, which never existed | read the file | replaced by tests of the real CLI |
| `jsmin` corrupts template literals: `` `x ${1+1} y` `` became `x 2y` | node round-trip test failed | switched to rjsmin; rjsmin still corrupts a template nested inside `${}` (probed), so those files are left unminified |
| `write_sidecars` compressed Brotli twice; `compress()` returned a size, not bytes | read | `compress()` returns `(gzip, brotli)` bytes; test updated |
| Sidecars could be larger than the source | tiny files | sidecar written only if smaller |

## Verified here (not from web sources)
- 39 pytest tests pass (0.1.0; see "0.3.0 verification" below for the current suite and counts).
- Generated nginx snippet: `nginx -t` OK on nginx 1.24; curl showed `Content-Encoding: gzip` served from the `.gz` sidecar (48 B for a 1500 B CSS), `Cache-Control: public, max-age=31536000, immutable` on the hashed file, `no-cache` on HTML, `Vary: Accept-Encoding`.
- Not verified: Brotli serving (nginx here has no ngx_brotli), the Caddy snippet, behaviour on real-world sites.

## Web sources
**Cache headers for fingerprinted files** (immutable + one-year max-age; HTML short or no-cache; immutable only safe when the URL changes with content)
- https://specification.website/spec/performance/cache-control/
- https://stackharbor.com/en/knowledge-base/nginx-static-asset-caching/
- https://csswizardry.com/2023/10/what-is-the-maximum-max-age/
- https://docs.serverpod.dev/next/concepts/web-server/static-files.md (same rule, immutable reserved for cache-busted assets)

**Serving precompressed files**
- nginx `gzip_static` is in the default distribution; `brotli_static` needs the ngx_brotli module; both can be used together: https://docs.rs/static-compress
- ngx_brotli static module serves pre-compressed `.br` files: https://github.com/globalopsteam/ngx_brotli (mirror of google/ngx_brotli)
- Caddy `file_server { precompressed ... }` looks for `.br`/`.gz`/`.zst` sidecars in the listed order: https://caddyserver.com/docs/caddyfile/directives/file_server
- Precompressing removes per-request CPU cost: https://irbr.ru/en/blog/nginx-brotli-gzip-static-optimization/

**Inlining images as base64**: base64 is ~33% larger; small icons benefit, large images and shared images do not.
The sources **disagree on the break-even**: roughly 1 KB on HTTP/2 (freetoolonline image guide), ~4 KB (freetoolonline base64 guide, HTTP/1.1-era), under ~8 KB (moda.app). `--inline-small` therefore has no default; README suggests 2048.
- https://openreplay.com/tools/image-to-base64/
- https://freetoolonline.com/guides/en/base64-when-to-use-and-when-not-to.html
- https://freetoolonline.com/guides/en/image-to-base64-embed-in-html-vs-link.html
- https://dev.to/jeromell/when-and-when-not-to-inline-images-as-base64-2abo
- https://moda.app/tools/image-to-base64 (SVG: URL-encode instead of base64, usually smaller)

**`defer`**: runs after parsing in document order; no effect on module scripts (deferred by default).
- https://developer.mozilla.org/en/HTML/Element/Script
- https://rocketvalidator.com/html-validation/a-script-element-with-type-module-must-not-have-a-defer-attribute

**WebP**: Google reports lossless WebP 26% smaller than PNG, lossy 25-34% smaller than comparable JPEG: https://developers.google.com/speed/webp (these are Google's figures, results vary per image, which is why `--webp` keeps the original unless WebP is actually smaller).

## From the conversation
The EanHost logo write-up supplied by the user (1,176-byte WebP, base64 expansion to 1,568 characters, HTML growing by 3,627 bytes) is the reason for the inline-size warning and the "embedding does not compress" note.

## 0.3.0 verification (2026-11-04)

Environment: Windows 10.0.26200, Python 3.12.10, Node v24.19.0, click 8.4.2, htmlmin 0.1.12,
cssmin 0.2.0, rjsmin 1.3.0, brotli 1.2.0, Pillow 12.2.0 (AVIF support: yes), pytest 9.1.1.
No nginx or Caddy binary is installed here; no Python 3.13 interpreter is available.

### Test counts (reconciled)

- 0.1.0 verification note above: 39 tests.
- 0.2.0 README: 49 tests (the suite as committed at HEAD when this work started).
- 0.3.0 checkpoint (2026-11-04): **130 tests**, all passing (`python -m pytest -q`, 10.82 s).
  This is historical; the browser-validation milestone below raised the total to 135, and
  image-orientation and identity-negotiation coverage raised the total to 138; the later
  animated-PNG regression raises the current total to 139.
  39 and 49 describe earlier commits, not the current suite.

### Bugs reproduced, then fixed (each has a regression test)

| Bug | Evidence before the fix | Fix |
|---|---|---|
| `Accept-Encoding: gzip;q=0` still served gzip; `br;q=0, gzip;q=1` served br; `*` ignored | HTTP probe against the dev server: substring matching | parse q-values; explicit `q=0` excludes, `*` covers unnamed encodings, highest q wins, br ties |
| WebP conversion overwrote an existing `a.webp` | probe: `to_webp` returned the rename and replaced the file's bytes | never overwrite; skip with a reason, and two sources cannot claim one target |
| Percent-encoded refs (`img/my%20file.png`) broke after hashing and were missed by inlining | probe: file renamed, reference left pointing at the old name | resolve refs with `unquote`; rewrite both raw and percent-encoded spellings |
| Hashing could rename onto an existing name | probe: the computed target replaced the colliding asset | skip with `already exists`, manifest reason recorded |
| Code-referenced CSS/JS were left unhashed silently | `hash_names` `safe` filter dropped them without recording | skip reason names the referrer; counted in `hashed_skipped` and warnings |
| AVIF two sources could claim one `.avif` | review of `to_avif` candidate collection | target set tracked while collecting; second source skipped |

### Compatibility and measurements

- Default and full-option builds produce **byte-identical output trees to HEAD**. Verified by
extracting HEAD with `git archive` into a temporary directory, building the same fixture with both
versions (default; `--hash --webp --inline-small 2048 --defer-scripts`), and comparing SHA-256 of
every output file: identical in both cases.
- AVIF vs WebP vs PNG on one synthetic 320x200 gradient PNG (65,218 B): lossless WebP 41,600 B
(36.2% smaller), lossless AVIF 9,944 B (84.8% smaller than PNG, 76.1% smaller than the WebP).
One fixture, not a general benchmark; AVIF keeps the PNG fallback, so the output directory grows by
the AVIF copy and only non-AVIF browsers fetch the original.
- Build wall time on the 7-file demo site: 247 ms first run, 101-102 ms warm (Windows, 3 runs).
- Manifest for a one-asset site: 429 bytes.

### Not verified here (blocked)

- **Python 3.13**: the cgi stand-in is tested by blocking `import cgi` in a fresh interpreter and
minifying HTML; no 3.13 interpreter exists on this machine, so the suite has not run on 3.13.
- **nginx / Caddy**: no binaries available. The nginx 1.24 result above is historical; the Caddy
snippet and real Brotli serving remain unverified.
- **GitHub Actions**: workflow added (`.github/workflows/ci.yml`, Python 3.10-3.13 on Linux and
Windows) but never executed here.

### Decisions

- No new runtime dependencies. AVIF uses the already-optional Pillow (11.3+ ships AVIF); the cgi
stand-in is a few lines instead of replacing htmlmin.
- Symlinks that resolve outside the input (or are broken) are skipped with a warning instead of
being followed into the output.
- Percent-encoded references are rewritten in both spellings, because a file with a space can only
be linked as `%20`.
- `htmlmin` never calls `cgi` (only imports it), so the stand-in is behaviourally inert. Minifier
output was probed (`calc()`, grid, `pre`, `textarea`, conditional comments, attribute values with
`>`) before its edge cases were written into tests.

## Browser validation (2026-11-06)

Environment: Windows 10.0.26200, headed Google Chrome 157.0.8103.39, Python 3.12.10, Node v24.19.0,
Pillow 12.2.0 (AVIF and WebP available), pytest 9.1.1, brotli 1.2.0. The full report is
`docs/BROWSER-VALIDATION.md`; the fixture and raw captures are local-only under `__development/experiments/`.

- **139 pytest tests pass** after the browser-found, image-orientation and animated-PNG fixes (130 before this
  milestone).
- A 38-file / 1,142,345-byte fixture with six routes was built eight ways (default, hash, WebP,
  AVIF, full, defer, AVIF+hash) and loaded in Chrome: 48 page loads, zero exceptions, zero failed
  requests, zero broken images (the absent favicon is excluded and reported separately).
- **Runtime filename prefix bug fixed:** `"/img/runtime-" + n + ".png"` was renamed under
  `--hash`/`--webp`, causing a browser 404. The previous regex only recognized a literal ending at
  the directory separator. The new detector extracts the literal prefix before the dynamic part
  and matches it at a path-segment boundary; unrelated siblings are still hashable. Three
  regression tests cover hash, template-literal extension handling and WebP.
- **Defer filename bug fixed:** `--defer-scripts` mistook `defer`/`async` inside a `src` filename for
  an existing attribute, so `defer-dep.js` and `defer-use.js` were skipped. Attribute and module
  detection are token-based now; one regression test covers it, and Chrome confirms deferred order.
- **Negative inline threshold rejected:** `--inline-small -1` previously exited 0 as a no-op. It now
  exits 2 with a clear error; a regression test covers the CLI.
- **EXIF orientation and ICC preservation:** WebP/AVIF conversion re-encoded the raw pixel matrix
  and dropped orientation metadata, so an oriented JPEG displayed sideways after conversion. The
  transforms now apply `ImageOps.exif_transpose()` before encoding and carry the ICC profile into
  the new format. Two tests assert the displayed dimensions, absence of a stale orientation tag and
  byte-identical ICC profile.
- Inline sweep: 0–128 bytes was byte-neutral; 256–2048 dropped one request but added 437 bytes;
  16 KiB and 64 KiB increased output by 8% and 43%. No threshold caused browser errors.
- Server probes confirmed Brotli/gzip/identity, q=0, wildcard, 406 when identity is explicitly
  rejected, HEAD, Vary, no-cache HTML and immutable hashed assets. The AVIF output directory grew because the original fallback is kept;
  an AVIF-capable browser still fetched fewer bytes.
- **Not verified:** no nginx/Caddy binaries, no Python 3.13 (3.11 lacks the test dependencies), no
  GitHub Actions execution, no Firefox/Safari/mobile browser, no real CDN or throttled network.
- The earlier 0.3.0 statement that default and full-option output was byte-identical to HEAD was a
  checkpoint before these two transform fixes. It remains true for that old fixture, but sites with
  runtime filename prefixes or defer/async-looking filenames now intentionally produce corrected
  output; the current deterministic-repeat check is against the current code, not HEAD.
