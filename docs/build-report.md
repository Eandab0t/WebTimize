# Build report (`--report`)

`--report build-report.json` writes a machine-readable summary of a build. The shape is versioned by
`schema_version` (currently `1`); added blocks are additive.

```json
{
  "schema_version": 1,
  "tool": { "name": "webtim", "version": "0.3.0" },
  "input": "/abs/site",
  "output": "/abs/dist",
  "dry_run": false,
  "sizes_are_estimated": false,
  "started_at": "2026-11-04T12:00:00+00:00",
  "status": "ok",
  "report": "/abs/build-report.json",
  "counts": {
    "discovered": 12, "processed": 12, "skipped": 0, "failed": 0, "output_files": 13
  },
  "sizes": {
    "original_bytes": 184320,
    "output_bytes": 141900,
    "saved_bytes": 42420,
    "saved_percent": 23.0,
    "gzip_wire_bytes": 39120,
    "brotli_wire_bytes": 34210
  },
  "transformations": {
    "minified": 8, "minified_unchanged": 2,
    "webp": 2, "webp_skipped": 0, "avif": 0, "avif_skipped": 0,
    "inlined": 1, "hashed": 6, "hashed_skipped": 1, "deferred": 3, "dropped": 1
  },
  "files": [
    { "path": "index.html", "bytes": 412, "gzip_bytes": 208, "brotli_bytes": 190 }
  ],
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
  "warnings": [],
  "errors": [],
  "duration_ms": 214
}
```

## Fields

- `status`: `ok`; `partial` when a transform failed and its file was copied unchanged;
  `no_web_files` when the input has no HTML/CSS/JS (nothing is written and `files` is empty);
  `failed` when the build raised. A failed report still contains `tool`, `input`, `output`, `dry_run`,
  `started_at`, `errors` (the failure message) and `warnings`; `counts`, `sizes`,
  `transformations` and `files` may be empty because the build stopped before measuring.
- The report is written atomically (temporary file in the same directory, then rename), including
  the failure report, so a reader never sees a half-written JSON file.
-`--report` may not be inside the input or output directory, may not be an existing directory, and
may not be the same path as `--manifest` or the generated server config. Those are rejected before
any file is touched. The browser validation also checked report counts, byte sums, sidecar lengths
and schema shape against the real output; see [BROWSER-VALIDATION.md](BROWSER-VALIDATION.md).
- `counts.processed`: discovered minus skipped (non-UTF-8 files). `failed` counts files whose
  minifier raised; they are copied unchanged.
- `sizes.saved_percent`: `1 - output/original`, rounded to one decimal.
- `sizes.gzip_wire_bytes` / `brotli_wire_bytes`: sum of the smaller of each file's sidecar and its
  uncompressed size, excluding HTTP overhead.
- `transformations.minified` + `minified_unchanged` equals the number of HTML/CSS/JS files processed.
- `files[]`: per output file, with the sidecar sizes (`0` when no sidecar was written).
- `dry_run` (and `sizes_are_estimated`): both `true` for `--dry-run`; no output directory is created,
  but the report is still written.

## Diagnostics (0.3.0, additive)

Every number is measured from the build output; nothing is estimated.

- `largest_files`: up to five output files by byte size.
- `largest_compression_savings`: up to five files by `bytes - min(gzip, brotli)`; `wire_bytes` is
  what a server would actually send for that encoding.
- `unrewritten_assets`: assets left unhashed, with the same reason used in the manifest.
- `transformations_increased_size`: files whose output is larger than the input file that produced
  it (for example, HTML after inlining images), with `delta_bytes` = output − input.

`warnings` lists the human-readable messages printed during the build, de-duplicated; the report
never hides a skip or a failed transform. `diagnostics` is only present when the build reached the
optimization stage; on `failed` and `no_web_files` it is omitted rather than fabricated.

The report is part of the build transaction: it is written after the output, manifest and server
config are in place. If writing it fails, the new output is rolled back to the previous build and
the command fails, so a report never describes an output that was not published.
