# Asset manifest (`--manifest`)

`webtim ./site -o ./dist --hash --manifest assets.json` writes a JSON file that maps every hashable asset's
original path to the name it ships under. Deployment pipelines, CDNs and static-site frameworks can
read it instead of guessing hashed names.

## Shape (schema v1)

```json
{
  "schema_version": 1,
  "tool": { "name": "webtim", "version": "0.3.0" },
  "hash_algorithm": "sha256",
  "hash_length": 8,
  "dry_run": false,
  "assets": [
    {
      "original": "css/app.css",
      "final": "css/app.5bd292c2.css",
      "sha256": "5bd292c2…",
      "type": "text/css",
      "hashed": true,
      "reason": null
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `schema_version` | `1`; increments only on a breaking change to the shape |
| `hash_algorithm`, `hash_length` | The content hash and the number of hex characters used in a name |
| `dry_run` | `true` when written by `--dry-run` (the mapping is what a real build would produce) |
| `assets[]` | One entry per hashable asset, sorted by `original` (byte order) |
| `original` | Path relative to the input directory, POSIX separators |
| `final` | Path relative to the output directory; equals `original` when not renamed |
| `sha256` | Hex digest of the bytes that ship; for a hashed asset these are the bytes its name encodes |
| `type` | MIME type from the extension (`text/css`, `text/javascript`, `image/png`, `font/woff2`, …) |
| `hashed` | Whether the file was renamed |
| `reason` | `null` when hashed; otherwise why it kept its name |

## Which assets are listed

Every file whose extension is hashable: CSS, JavaScript, fonts (`.woff`, `.woff2`, `.ttf`, `.otf`)
and images (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.avif`, `.svg`). HTML is not hashable — it is
the entry point and is served `no-cache`.

## Skip reasons

- `absolute URL reference, not renamed` — an `og:image`/manifest-style absolute URL still names it,
  so its URL cannot change; one copy keeps both references working.
- `<prefix>*<ext>: assembled at runtime in <file>, not renamed` — the page builds the path in
  JavaScript; renaming would break a URL no regex can rewrite.
- `referenced by <file>, not renamed` — a CSS/JS file names it (e.g. `import "./util.js"`);
  renaming it would break the import.
- `target name already exists, not renamed` — the computed name is already taken in the output.
- `hashing not enabled` — the manifest was requested without `--hash`.

Each of these is also emitted as a build warning, so nothing is skipped silently.

## Determinism

The manifest depends only on the input bytes and the selected options:

- entries are sorted by `original`;
- `sha256` comes from the shipped bytes (CSS/JS are hashed after their references are rewritten, so
  changing an image changes the CSS/JS hash too);
- the JSON is serialised with sorted keys and a trailing newline.

Building the same input twice with the same options produces byte-identical manifests and
byte-identical output. `started_at` deliberately does not appear in the manifest. The browser
validation checked every entry's `final` file and `sha256` against the actual output; see
[BROWSER-VALIDATION.md](BROWSER-VALIDATION.md).

## Consuming it

```python
mapping = {a["original"]: a["final"] for a in json.load(open("assets.json"))["assets"] if a["hashed"]}
url_for = lambda original: "/" + mapping.get(original, original)
```

## Limits

- The manifest describes assets webtim could see by reference; runtime-constructed URLs are listed
  as skipped, not guessed. This includes literal filename prefixes such as `"/img/runtime-" + n`
  as well as directory prefixes.
- AVIF conversion keeps the original file; both the original and the `.avif` appear as separate
  entries.
- The manifest is written atomically after the output is promoted, so it always describes the
  published build. If the build fails, any previous manifest is left untouched.
- `--manifest` may not be inside the output directory (promotion replaces it) or inside the input
  directory (a build must not modify its input), and may not share a path with `--report` or the
  generated server config. Those are rejected before the build starts.
