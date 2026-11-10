"""Server snippets that serve the .br/.gz sidecars and set cache headers for hashed files."""
import re

from webtim.transforms import HASHED_RE

LONG = "public, max-age=31536000, immutable"  # fingerprinted files never change under the same URL


def _quoted_path(root: str) -> str:
    """Quote a filesystem path for a config string without allowing a new directive.

    A directory name is user input. Nginx terminates a directive at `;` and Caddy at a newline,
    so an unquoted root such as `out; add_header X yes` becomes two directives. Quotes are enough
    for punctuation; line breaks and NUL cannot be represented safely and are rejected.
    """
    if re.search(r"[\r\n\x00]", root):
        raise ValueError("server config path must not contain line breaks or NUL bytes")
    return root.replace("\\", "\\\\").replace('"', '\\"')


def nginx(root: str) -> str:
    quoted = _quoted_path(root)
    return f"""# Include inside a server {{ }} block.
root "{quoted}";
gzip_static on;
gzip_vary on;
# brotli_static needs the ngx_brotli module; uncomment only if `nginx -V` lists it.
# brotli_static on;

# Hashed assets (name.<hash>.ext): cache for a year, never revalidate.
location ~* "{HASHED_RE}" {{
    add_header Cache-Control "{LONG}";
}}

# Everything else: always revalidate (ETag), so HTML updates show up immediately.
location / {{
    add_header Cache-Control "no-cache";
}}
"""


def caddy(root: str) -> str:
    quoted = _quoted_path(root)
    return f"""# Caddyfile site block body. Caddy serves name.br / name.gz when the client accepts them.
root * "{quoted}"
@hashed path_regexp hashed {HASHED_RE}
header @hashed Cache-Control "{LONG}"
header ?Cache-Control "no-cache"
file_server {{
    precompressed br gzip
}}
"""
