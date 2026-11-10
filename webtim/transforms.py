"""Whole-site transforms. A site is a dict {relative PurePosixPath: bytes}.

Every transform is opt-in (see cli.py) because each one changes the HTML/asset graph.
"""
import base64
import hashlib
import io
import mimetypes
import posixpath
import re
from pathlib import PurePosixPath
from urllib.parse import quote, unquote

TEXT = {".html", ".css", ".js"}
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
        ".webp": "image/webp", ".avif": "image/avif", ".svg": "image/svg+xml"}
HASHABLE = {".css", ".js", ".woff", ".woff2", ".ttf", ".otf"} | set(MIME)
HASH_LEN = 8
MAX_IMAGE_PIXELS = 40_000_000  # ~40 MP; larger images are skipped rather than decoded
# One regex shared by serve.py and the nginx/Caddy snippets so "hashed" means the same everywhere.
HASHED_RE = r"\.[0-9a-f]{%d}\.[A-Za-z0-9]+$" % HASH_LEN

_TYPE = {".css": "text/css", ".js": "text/javascript", ".woff": "font/woff",
         ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf"}

Site = dict  # PurePosixPath -> bytes


def asset_type(path: PurePosixPath) -> str:
    """MIME type for an asset path (used by the manifest; inlining has its own image map)."""
    suffix = path.suffix.lower()
    return MIME.get(suffix) or _TYPE.get(suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _text(site: Site, p: PurePosixPath) -> str:
    return site[p].decode("utf-8")


def _open_image(data: bytes, path: PurePosixPath):
    """Open an image header and enforce a pixel ceiling before any full decode."""
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    pixels = img.width * img.height
    if pixels > MAX_IMAGE_PIXELS:
        raise ValueError(
            f"{path}: {img.width}x{img.height} ({pixels} pixels) exceeds the "
            f"{MAX_IMAGE_PIXELS}-pixel conversion limit"
        )
    return img


def _prepare_image(img):
    """Apply EXIF orientation before re-encoding; the new format drops the tag."""
    from PIL import ImageOps

    source_format = img.format
    if img.getexif().get(274) not in (None, 1):
        img = ImageOps.exif_transpose(img)
    profile = img.info.get("icc_profile")
    return img, source_format, ({"icc_profile": profile} if profile else {})


def _resolve(page: PurePosixPath, ref: str) -> PurePosixPath | None:
    """Map a reference found in `page` to a site path; None for external/data/anchor refs.

    Percent-encoded refs (`img/my%20file.png`) resolve to the file on disk, so a rename can
    rewrite every spelling of the same URL.
    """
    ref = re.split(r"[?#]", ref, maxsplit=1)[0]
    if not ref or re.match(r"^([a-z][a-z0-9+.-]*:|//)", ref, re.I):
        return None
    ref = unquote(ref)
    base = "/" if ref.startswith("/") else str(page.parent)
    path = posixpath.normpath(posixpath.join(base, ref)).lstrip("/")
    return PurePosixPath(path)


def _spellings(path: str) -> list[str]:
    """The literal spellings one URL path can take: raw, and percent-encoded when it differs."""
    encoded = quote(path, safe="/")
    return [path] if encoded == path else [path, encoded]


def rewrite_refs(site: Site, renames: dict) -> None:
    """Replace every quoted/url() reference to a renamed file, relative or root-relative.

    Both the raw and the percent-encoded spelling of a URL are rewritten, because a file with a
    space in its name can only be referenced as `my%20file.png` in HTML.
    """
    for page in [p for p in site if p.suffix in TEXT]:
        text = _text(site, page)
        for old, new in renames.items():
            rel_old = posixpath.relpath(old, page.parent)
            rel_new = posixpath.relpath(new, page.parent)
            pairs = []
            for o, n in ((rel_old, rel_new), ("/" + str(old), "/" + str(new))):
                pairs.extend(zip(_spellings(o), _spellings(n)))
            seen = set()
            for o, n in pairs:
                if o in seen:  # the raw and encoded spellings can coincide
                    continue
                seen.add(o)
                text = re.sub(r"(?<![\w./-])(\./)?" + re.escape(o) + r"(?=[\s\"'`)?#>,;]|$)",
                              lambda m, n=n: (m.group(1) or "") + n, text)
        site[page] = text.encode("utf-8")


def _rename(site: Site, moves: dict) -> None:
    """moves: {old: (new, new_bytes)}. Rewrites refs, but keeps `old` when some text still names it
    (og:image / manifest absolute URLs cannot be rewritten safely, so both copies ship)."""
    old_bytes = {o: site.pop(o) for o in moves}
    for o, (n, b) in moves.items():
        site[n] = b
    rewrite_refs(site, {o: n for o, (n, _) in moves.items()})
    corpus = "\n".join(_text(site, p) for p in site if p.suffix in TEXT)
    for o in moves:
        # word-boundary match: `myicon.png` must not keep `icon.png` alive
        if re.search(r"(?<![\w.-])" + re.escape(o.name) + r"(?![\w-])", corpus):
            site[o] = old_bytes[o]


# shortcut: no <picture> fallback for WebP, every current browser decodes it; use --avif if you need one
def to_webp(site: Site, skipped: dict | None = None) -> dict:
    """PNG/JPEG -> WebP when Pillow is installed and the result is smaller. Returns the renames.

    An existing `.webp` is never overwritten, and two source images that would share one WebP name
    are left alone: both cases would silently destroy a file.
    """
    try:
        from PIL import Image
    except ImportError:
        return {}
    unsafe = unsafe_renames(site)
    if skipped is not None:
        skipped.update(unsafe)
    moves = {}
    taken = set(site)
    for p in [p for p in site if p.suffix.lower() in {".png", ".jpg", ".jpeg"} and p not in unsafe]:
        try:
            img, source_format, extra = _prepare_image(_open_image(site[p], p))
            if getattr(img, "is_animated", False):
                if skipped is not None:
                    skipped[p] = f"{p}: animated image, not converted to WebP"
                continue
            buf = io.BytesIO()
            # lossless for PNG keeps pixels exact; JPEG is already lossy so q80 is a fair match
            img.save(buf, "WEBP", lossless=(source_format == "PNG"), quality=80, method=6, **extra)
        except Exception as exc:
            if skipped is not None:
                skipped[p] = f"{p}: could not be converted to WebP, kept as is ({exc})"
            continue
        target = p.with_suffix(".webp")
        if target in taken:
            if skipped is not None:
                skipped[p] = f"{target}: already exists, {p} not converted"
            continue
        if len(buf.getvalue()) < len(site[p]):
            moves[p] = (target, buf.getvalue())
            taken.add(target)
    _rename(site, moves)
    return {o: n for o, (n, _) in moves.items()}


_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_SRC_ATTR = re.compile(r"""\bsrc\s*=\s*["']([^"']+)["']""", re.I)


def _inside_picture(html: str, position: int) -> bool:
    head = html[:position].lower()
    return head.rfind("<picture") > head.rfind("</picture>")


# shortcut: AVIF is only added where an <img src> can carry a <picture> fallback; CSS-only refs
# cannot, so they are reported and left alone
def to_avif(site: Site, skipped: dict | None = None, quality: int = 80) -> dict:
    """PNG/JPEG -> AVIF when smaller AND an `<img src>` can carry a `<picture>` fallback.

    The original file is always kept: it is the fallback source. Returns {original: avif}.
    Images only named from CSS, srcset or a runtime path are not converted (no safe fallback).
    """
    try:
        from PIL import Image
    except ImportError:
        return {}
    unsafe = unsafe_renames(site)
    if skipped is not None:
        skipped.update(unsafe)
    candidates = {}
    taken = set(site)
    for p in [p for p in site if p.suffix.lower() in {".png", ".jpg", ".jpeg"} and p not in unsafe]:
        if p.with_suffix(".avif") in taken:
            if skipped is not None:
                skipped[p] = f"{p.with_suffix('.avif')}: already exists, {p} not converted"
            continue
        try:
            img, source_format, extra = _prepare_image(_open_image(site[p], p))
            if getattr(img, "is_animated", False):
                if skipped is not None:
                    skipped[p] = f"{p}: animated image, not converted to AVIF"
                continue
            buf = io.BytesIO()
            img.save(buf, "AVIF", quality=quality, lossless=(source_format == "PNG"), **extra)
        except Exception as exc:
            if skipped is not None:
                skipped[p] = f"{p}: could not be converted to AVIF, kept as is ({exc})"
            continue
        if len(buf.getvalue()) < len(site[p]):
            candidates[p] = buf.getvalue()
            taken.add(p.with_suffix(".avif"))  # two sources must not claim one target

    used = set()
    for page in [p for p in site if p.suffix == ".html"]:
        html = _text(site, page)

        def wrap(match, page=page):
            tag = match.group(0)
            if re.search(r"\bsrcset\b", tag, re.I):
                return tag  # an existing srcset needs hand-written <source> entries
            found = _SRC_ATTR.search(tag)
            if not found or _inside_picture(html, match.start()):
                return tag
            target = _resolve(page, found.group(1))
            if target not in candidates:
                return tag
            ref, name = found.group(1), target.with_suffix(".avif").name
            if "%" in ref:
                name = quote(name)
            new_ref = ref[: ref.rfind("/") + 1] + name
            used.add(target)
            return f'<picture><source srcset="{new_ref}" type="image/avif">{tag}</picture>'

        site[page] = _IMG_TAG.sub(wrap, html).encode("utf-8")

    moves = {}
    for target, data in candidates.items():
        if target in used:
            moves[target] = (target.with_suffix(".avif"), data)
        elif skipped is not None:
            skipped[target] = (
                f"{target}: only named where no <picture> fallback can be added "
                "(CSS, srcset or runtime), not converted"
            )
    for new, data in moves.values():
        site[new] = data  # the original stays: it is the fallback
    return {o: n for o, (n, _) in moves.items()}


_REF = re.compile(r"""((?:src\s*=\s*|url\()\s*["']?)([^"'\s)>]+)""", re.I)


# shortcut: inlining into a page shared by many routes defeats caching, keep the threshold small
def inline_small(site: Site, max_bytes: int, dropped: list | None = None) -> int:
    """Replace refs to images <= max_bytes with data: URIs, then drop files nothing references. Returns count."""
    inlined = set()

    def sub(page):
        def f(m):
            target = _resolve(page, m.group(2))
            data = site.get(target)
            if data is None or target.suffix.lower() not in MIME or len(data) > max_bytes:
                return m.group(0)
            inlined.add(target)
            mime = MIME[target.suffix.lower()]
            if target.suffix.lower() == ".svg":  # SVG is text: URL-encoding beats base64's +33%
                return f"{m.group(1)}data:{mime},{quote(data.decode('utf-8'), safe='/:')}"
            return f"{m.group(1)}data:{mime};base64,{base64.b64encode(data).decode()}"
        return f

    for page in [p for p in site if p.suffix in {".html", ".css"}]:
        site[page] = _REF.sub(sub(page), _text(site, page)).encode("utf-8")
    still_used = "\n".join(_text(site, p) for p in site if p.suffix in TEXT)
    for t in inlined:
        if t.name not in still_used:
            del site[t]
            if dropped is not None:
                dropped.append(t)
    return len(inlined)


_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.I | re.S)


def _is_inline_code(attrs: str, body: str) -> bool:
    """Classic inline script. Data blocks (JSON-LD, importmap) and modules don't break under defer."""
    if re.search(r"\bsrc\s*=", attrs, re.I) or not body.strip():
        return False
    t = re.search(r"type\s*=\s*[\"']?([^\"'\s>]+)", attrs, re.I)
    return not t or t.group(1).lower() == "text/javascript"


def _has_attribute(attrs: str, name: str) -> bool:
    """Whether a boolean/attribute token appears; an `async`/`defer` filename does not count."""
    return bool(re.search(rf"(?:^|\s){re.escape(name)}(?=\s|=|$)", attrs, re.I))


def _has_module_type(attrs: str) -> bool:
    return bool(re.search(r"(?:^|\s)type\s*=\s*[\"']?module[\"']?(?=\s|$)", attrs, re.I))


def defer_scripts(site: Site) -> int:
    """Add `defer` to external classic scripts. Skips any page that also has inline code,
    because deferring an external script can break an inline script that uses its globals."""
    count = 0
    for page in [p for p in site if p.suffix == ".html"]:
        html = _text(site, page)
        tags = _SCRIPT.findall(html)
        if any(_is_inline_code(a, body) for a, body in tags):
            continue

        def f(m):
            nonlocal count
            attrs = m.group(1)
            if (not re.search(r"\bsrc\s*=", attrs, re.I)
                    or _has_attribute(attrs, "defer") or _has_attribute(attrs, "async")
                    or _has_module_type(attrs)):
                return m.group(0)  # module scripts already defer; async/defer already set
            count += 1
            return f"<script{attrs} defer>{m.group(2)}</script>"

        site[page] = _SCRIPT.sub(f, html).encode("utf-8")
    return count


# A runtime path can concatenate anywhere in the filename, not only after a directory:
#   "img/" + name + ".png"; "/img/runtime-" + n + ".png"
# The literal before the first dynamic part is the only safe thing to match. A template literal
# additionally tells us the extension when it is literal (`${name}.png`).
_RUNTIME_CONCAT = re.compile(r"""([\"'])([^\"'\\\r\n]*?)\1\s*\+""")
_RUNTIME_TEMPLATE = re.compile(r"""`([^`$]*)\$\{[^}]*\}([^`$]*)`""")


def _runtime_prefixes(site: Site) -> dict:
    """{literal path prefix: [(file, extension or None)]} for paths assembled at runtime.

    A JS string is resolved against the *document*, not the script file, so the prefix is matched
    as a path suffix instead of being resolved against the file that contains it. Matching only the
    literal before the dynamic part keeps `img/runtime-` from marking every image in `img/`.
    """
    found = {}
    for page in [p for p in site if p.suffix in TEXT]:
        text = _text(site, page)
        refs = []
        for match in _RUNTIME_CONCAT.finditer(text):
            prefix = match.group(2)
            if "/" in prefix and not re.match(r"^(?:[a-z][a-z0-9+.-]*:|//)", prefix, re.I):
                refs.append((prefix, None))
        for match in _RUNTIME_TEMPLATE.finditer(text):
            prefix, suffix = match.group(1), match.group(2)
            if "/" in prefix and not re.match(r"^(?:[a-z][a-z0-9+.-]*:|//)", prefix, re.I):
                extension = re.match(r"\.([A-Za-z0-9]+)", suffix)
                refs.append((prefix, "." + extension.group(1).lower() if extension else None))
        for prefix, extension in refs:
            prefix = prefix.lstrip("./")
            if prefix:
                found.setdefault(prefix, []).append((str(page), extension))
    return found


def unsafe_renames(site: Site) -> dict:
    """{path: reason} for assets a rename would break: an absolute URL or a runtime-built path names them."""
    corpus = "\n".join(_text(site, p) for p in site if p.suffix in TEXT)
    runtime = _runtime_prefixes(site)
    unsafe = {}
    for p in [p for p in site if p.suffix.lower() in HASHABLE]:
        if re.search(r"(?i)(?:https?:)?//[^\s\"'<>()]*" + re.escape(p.name), corpus):
            unsafe[p] = f"{p}: absolute URL reference, not renamed"
            continue
        path = p.as_posix()
        for prefix, refs in runtime.items():
            extensions = {e for _, e in refs if e is not None}
            if extensions and p.suffix.lower() not in extensions:
                continue
            # Match a path segment boundary, not an arbitrary filename substring.
            if path.startswith(prefix) or ("/" + prefix) in path:
                ext = sorted(extensions)[0] if extensions else ""
                unsafe[p] = f"{prefix}*{ext}: assembled at runtime in {refs[0][0]}, not renamed"
                break
    return unsafe


# shortcut: refs built at runtime ("img/" + name + ".png") are invisible here, use a bundler for those sites
def hash_names(site: Site, skipped: dict | None = None, manifest: list | None = None) -> dict:
    """Rename assets to name.<sha256[:8]>.ext so they can be cached forever. Returns the renames.

    An asset named by an absolute URL or by a path the page builds at runtime is left alone:
    the new name cannot be written back, and shipping both the original and a hashed copy only
    grows the output. A css/js file named by another css/js file is left alone too, and the skip is
    recorded (with its referrer) instead of being silent.

    Pass 1 hashes leaves (images/fonts) and rewrites refs; pass 2 hashes css/js, whose bytes
    now include the leaf renames.

    When `manifest` is a list, an entry is appended for every hashable asset (sorted by original
    path): original, final, sha256 of the shipped bytes, MIME type, hashed flag and skip reason.
    """
    def rename(paths):
        moves = {}
        for p in paths:
            target = p.with_name(f"{p.stem}.{hashlib.sha256(site[p]).hexdigest()[:HASH_LEN]}{p.suffix}")
            if target in site and target != p:
                if skipped is not None:
                    skipped[p] = f"{p}: {target} already exists, not renamed"
                continue
            moves[p] = (target, site[p])
        _rename(site, moves)
        return {o: n for o, (n, _) in moves.items()}

    originals = {p: site[p] for p in site if p.suffix.lower() in HASHABLE}
    blocked = unsafe_renames(site)
    if skipped is not None:
        skipped.update(blocked)

    renames = rename([p for p in site if p.suffix.lower() in HASHABLE - {".css", ".js"}
                      and p not in blocked])
    code = [p for p in site if p.suffix in {".css", ".js"}]
    others = {p: _text(site, p) for p in code}
    safe = []
    for p in code:
        if p in blocked:
            continue
        referenced = [q for q, t in others.items() if q != p and p.name in t]
        if referenced:
            if skipped is not None:
                skipped[p] = f"{p}: referenced by {referenced[0]}, not renamed"
            continue
        safe.append(p)
    renames.update(rename(safe))

    if manifest is not None:
        for original in sorted(originals, key=str):
            final = renames.get(original)
            if final is not None and original in site:
                # rewritable text was rewritten, but an unrewritable reference still names the old
                # file, so both copies ship; the original name remains the canonical final
                final = None
            shipped = final or original
            data = site.get(shipped, originals[original])
            reason = None if final is not None else (skipped or {}).get(
                original, "not renamed: no safely rewritable reference found")
            manifest.append({
                "original": str(original),
                "final": str(shipped),
                "sha256": hashlib.sha256(data).hexdigest(),
                "type": asset_type(shipped),
                "hashed": final is not None,
                "reason": reason,
            })
    return renames
