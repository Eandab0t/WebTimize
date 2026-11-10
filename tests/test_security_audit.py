import struct
import zlib
from pathlib import Path, PurePosixPath

import pytest

from webtim import transforms as T
from webtim.build import build
from webtim.serve_config import caddy, nginx


def _png_header(width, height):
    """A PNG with a valid IHDR but no real pixel payload: enough to trigger the pixel guard."""
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00"))
        + chunk(b"IEND", b"")
    )


def test_server_config_quotes_a_root_that_contains_directive_punctuation():
    root = "/srv/out; add_header X-Injected yes"
    n = nginx(root)
    c = caddy(root)
    assert 'root "/srv/out; add_header X-Injected yes";' in n
    assert 'root * "/srv/out; add_header X-Injected yes"' in c
    assert "\nadd_header X-Injected yes;" not in n


def test_server_config_rejects_line_breaks_that_cannot_be_quoted():
    with pytest.raises(ValueError, match="line breaks"):
        nginx("/srv/out\nlocation /pwn { }")
    with pytest.raises(ValueError, match="line breaks"):
        caddy("/srv/out\r\npwn")


def test_build_emits_a_quoted_root_for_a_punctuated_output_directory(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    out = tmp_path / "out; add_header X-Injected yes"
    build(site, out, emit_config="nginx")
    text = Path(str(out) + ".nginx.conf").read_text(encoding="utf-8")
    assert 'root "' in text
    assert "\nadd_header X-Injected yes;" not in text


@pytest.mark.parametrize("width,height", [(10000, 10000), (20000, 5000)])
def test_oversized_image_header_is_skipped_before_decoding(width, height):
    pytest.importorskip("PIL")
    skipped = {}
    moves = T.to_webp({PurePosixPath("huge.png"): _png_header(width, height)}, skipped=skipped)
    assert moves == {}
    reason = skipped[PurePosixPath("huge.png")]
    assert "pixels" in reason and "conversion limit" in reason


def test_normal_small_image_still_converts(tmp_path):
    PIL = pytest.importorskip("PIL")
    from PIL import Image

    image = Image.new("RGB", (16, 16), (10, 20, 30))
    buffer = __import__("io").BytesIO()
    image.save(buffer, "PNG")
    skipped = {}
    moves = T.to_webp({PurePosixPath("small.png"): buffer.getvalue()}, skipped=skipped)
    # A 16x16 PNG rarely shrinks as WebP, so assert the guard did not reject it by reason.
    assert "conversion limit" not in " ".join(skipped.values())
    assert set(moves) <= {PurePosixPath("small.png")}
