# Security and privacy audit — WebTimize Compressor

Date: 2026-11-06  
Scope: the local `webtim` package, its CLI and development server, generated server
configuration, and the Python dependency set.  
Environment: Windows 10.0.26200, Python 3.12.10, Pillow 12.2.0. No network services,
accounts or production data were used.

## Threat model

The normal user runs `webtim` against a directory they chose, on their own machine. The
important boundaries are:

- a site tree may contain untrusted files (a downloaded theme, a contributed asset, an
  archive extracted by another tool);
- the output directory name and the input filenames are user-controlled strings;
- the generated Nginx/Caddy snippet is copied into a server configuration and therefore
  must not let a filename add directives;
- the build must not read outside the input tree through links, and a failed build must
  not destroy the previous successful output.

The tool is not a sandbox for hostile build scripts. Running it against a directory that
another local process is actively rewriting is outside the normal trust model; that case
is listed under residual risk rather than treated as solved.

## Findings

| ID | Severity | Finding | Status |
| --- | --- | --- | --- |
| C-1 | Medium | Output-directory names were interpolated unquoted into generated Nginx/Caddy config, allowing directive injection. | Fixed |
| C-2 | Medium | WebP/AVIF conversion opened and decoded images without an explicit pixel ceiling, so a decompression bomb could exhaust memory. | Fixed |
| C-3 | Low | The repository had no `LICENSE` file and `pyproject.toml` had no license metadata, although every direct dependency is licensed. | Fixed: MIT license and PEP 639 metadata added |
| C-4 | Low | Dependencies are unpinned and cssmin is old/unmaintained; no advisory scan was available offline. | Accepted risk, documented |
| C-5 | Low | Symlink checks happen before `read_bytes()`, leaving a TOCTOU window if another process swaps a link during a build. | Residual, not reproduced |
| C-6 | Info | `webtim-serve` inherits stdlib symlink behaviour when serving a directory; it is loopback-only by default and intended for trusted output. | Documented |
| C-7 | Info | Non-image inputs are read whole into memory with no file-size cap. | Documented |

No high or critical issue was confirmed.

## C-1 — server-config injection through the output path

**Affected:** `webtim/serve_config.py`, reached from `webtim/build.py` when
`--emit-config nginx` or `--emit-config caddy` is used.

`nginx()` and `caddy()` used `root {root};` / `root * {root}` with the output path
verbatim. On Windows a directory name may legally contain `;` and spaces. A directory
named `out; add_header X-Injected yes` therefore produced:

```nginx
root C:\...\out; add_header X-Injected yes;
```

The second directive is parsed by Nginx. The same class of problem applies to newlines
and brace characters on filesystems that permit them.

**Reproduction (before the fix):**

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from webtim.build import build

with TemporaryDirectory() as td:
    root = Path(td)
    site = root / "site"; site.mkdir()
    (site / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    out = root / "out; add_header X-Injected yes"
    build(site, out, emit_config="nginx")
    print((Path(str(out) + ".nginx.conf")).read_text())
```

Observed line: `root C:\...\out; add_header X-Injected yes;`.

**Fix:** `serve_config._quoted_path()` double-escapes backslashes/quotes and wraps the
root in a quoted string; CR, LF and NUL are rejected with `ValueError` before a file is
written. Nginx now receives `root "…";`, Caddy receives `root * "…"`.

**Regression tests:** `tests/test_security_audit.py`:
- `test_server_config_quotes_a_root_that_contains_directive_punctuation`
- `test_server_config_rejects_line_breaks_that_cannot_be_quoted`
- `test_build_emits_a_quoted_root_for_a_punctuated_output_directory`

All pass. Real Nginx/Caddy syntax was **not** re-checked because neither binary is
installed here; run `nginx -t` / `caddy validate` in the target environment before
deploying the snippet. The previous README claim about a real Nginx 1.24 check predates
this quoting change and is now explicitly marked as such.

## C-2 — image decompression bomb

**Affected:** `webtim/transforms.py` (`to_webp`, `to_avif`).

Both paths used `PIL.Image.open(...)` and then `img.save(...)` without checking decoded
dimensions. Pillow's default warning threshold is advisory; a 100-megapixel header is
accepted with a warning and then decoded. A small crafted PNG can therefore allocate
hundreds of megabytes or more during conversion.

**Reproduction fixture:** a PNG header with `IHDR` 10000×10000 (100,000,000 pixels), a
minimal IDAT and IEND. The test feeds it to `to_webp` and expects a skip, not a decode.
The pre-fix path was not executed at that size deliberately; the guard is asserted before
`img.save`.

**Fix:** `transforms._open_image()` reads only the header, then rejects images above
`MAX_IMAGE_PIXELS = 40_000_000` before any pixel decode. The skip reason now includes the
underlying error, so the report says the image exceeded the conversion limit. The same
helper is used by WebP and AVIF.

**Regression tests:** `test_oversized_image_header_is_skipped_before_decoding` (two
dimension pairs) and `test_normal_small_image_still_converts`.

AVIF conversion still requires Pillow with AVIF support; the guard runs first.

## C-3 — licensing

The repository now has an MIT `LICENSE` and PEP 639 `license`/`license-files` metadata in
`pyproject.toml`. Installed direct dependencies report compatible permissive licenses:
Click BSD-3-Clause, htmlmin2 BSD, rjsmin Apache-2.0, cssmin (metadata has no license
expression in the installed build), Brotli MIT, Pillow MIT-CMU, pytest MIT. Third-party
notices still need to be preserved when dependencies are redistributed.

## C-4 — dependency maintenance

- Direct runtime dependencies: `click>=8.0`, `htmlmin2>=0.1.13`, `rjsmin>=1.2`,
  `cssmin>=0.2`, `brotli>=1.0`; optional `Pillow>=10`.
- No lockfile or hashes; a fresh install can pick arbitrary future versions within the
  ranges. Reproducible builds are not guaranteed.
- `cssmin` 0.2.0 is old. HTML minification uses the maintained `htmlmin2` distribution, which
  preserves the existing `htmlmin.Minifier` API without the removed `cgi` import.
- `pip-audit` is not installed and no advisory database was consulted. No network calls
  or third-party source uploads were made during this audit.

Recommended follow-up: pin a tested set for release, run `pip-audit` in a networked
release environment, and review the cssmin replacement before 1.0.

## C-5 — symlink TOCTOU

`collect()` resolves each link and rejects targets outside the input root, then later
calls `path.read_bytes()`. A process that can replace an allowed link between those two
operations could make the build read a different file. The included symlink tests cover
static outside/broken links; they do not prove race safety. A proper fix needs
descriptor-relative opens (`openat`/`O_NOFOLLOW`) or handle-based path verification and is
platform-specific; it was not added speculatively.

## C-6 / C-7 — server and memory limits

`webtim-serve` is a stdlib static server bound to `127.0.0.1` by default. It follows
symlinks in the directory it is given, like `SimpleHTTPRequestHandler`; serve only a
trusted build output. Non-image inputs are still loaded whole into memory. A site with
multi-gigabyte assets can exhaust memory; no cap was added because the tool has no
knowledge of a safe per-site limit. Document and monitor rather than silently truncate.

## Verification matrix

| Requested check | Result |
| --- | --- |
| Path traversal through references/output paths | `_resolve` normalises and ignores external refs; destinations are resolved and rejected inside input/output; tests pass. |
| Symlinks outside input | Rejected with warning; test passes. |
| Symlink races | Residual C-5. |
| Temporary directory permissions | `mkdtemp` creates staging with user-only permissions; config temp is written in the output parent. No world-writable staging observed. |
| Backup/staging collisions | Unique UUID backup; pre-existing backup preserved; tests pass. |
| Report/manifest collisions | Resolved-path equality check rejects collisions; tests pass. |
| Atomic replacement | `_atomic_write` uses `mkstemp` + `os.replace`; promotion rollback tests pass. |
| Nginx/Caddy injection | Fixed C-1. |
| Malformed HTML/CSS/JS | Minifiers fail closed to the original bytes; existing tests pass. |
| Oversized files / memory | Image guard C-2; non-image C-7. |
| Unsafe names / Unicode / spaces | Temporary fixture with `café index.html`, `style sheet.css` and `图片.png` built successfully with expected names and sidecars. |
| Permissions/metadata | Output files use process defaults; staging mode is not widened. Not changed. |
| Output containing unrelated files | Build warns and replaces after success; failure tests preserve the old tree. |

## Test evidence

```text
python -m pytest tests/test_security_audit.py -q
6 passed

python -m pytest -q
139 passed, 3 warnings in 13.11s
```

The six security regressions above still pass. The current suite is 139 tests
(`python -m pytest -q`: 139 passed, 46 warnings in the htmlmin2 verification environment).
The warnings are dependency deprecations and Pillow's expected oversized-fixture warning.

## Remaining risk

The build tool is designed for local, trusted input trees. Its strongest guarantees are
that it does not intentionally follow links outside the input, does not overwrite the
previous output on a failed build, and does not let a filename add server directives.
It is not a sandbox: concurrent filesystem mutation, very large non-image files, a
malicious local profile/deployment workflow and old minifiers remain risks. Third-party
license and notice obligations must still be checked for any bundled dependency. Real server validation is still required.
