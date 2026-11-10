import io
from pathlib import PurePosixPath as P

import pytest
from PIL import Image, ImageCms, features

from webtim import transforms as T


def oriented_jpeg():
    """A 240x120 JPEG whose EXIF orientation displays it as 120x240."""
    image = Image.linear_gradient("L").resize((240, 120)).convert("RGB")
    exif = Image.Exif()
    exif[274] = 6
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90, exif=exif.tobytes(), icc_profile=profile)
    return buffer.getvalue(), profile


def test_webp_applies_exif_orientation_and_keeps_icc_profile():
    source, profile = oriented_jpeg()
    site = {P("photo.jpg"): source}
    assert T.to_webp(site) == {P("photo.jpg"): P("photo.webp")}
    output = Image.open(io.BytesIO(site[P("photo.webp")]))
    assert output.size == (120, 240)
    assert output.getexif().get(274) is None
    assert output.info.get("icc_profile") == profile


def test_webp_does_not_flatten_an_animated_png():
    frames = [Image.new("RGBA", (4, 4), (20, 100, 70, 255)), Image.new("RGBA", (4, 4), (200, 30, 30, 255))]
    buffer = io.BytesIO()
    frames[0].save(buffer, "PNG", save_all=True, append_images=frames[1:], duration=50, loop=0)
    source = buffer.getvalue()
    site = {P("animation.png"): source}
    skipped = {}
    assert T.to_webp(site, skipped) == {}
    assert site[P("animation.png")] == source
    assert "animated" in skipped[P("animation.png")]


def test_avif_applies_exif_orientation_and_keeps_icc_profile():
    if not features.check("avif"):
        pytest.skip("Pillow has no AVIF support")
    source, profile = oriented_jpeg()
    site = {P("index.html"): b'<img src="photo.jpg">', P("photo.jpg"): source}
    assert T.to_avif(site) == {P("photo.jpg"): P("photo.avif")}
    output = Image.open(io.BytesIO(site[P("photo.avif")]))
    assert output.size == (120, 240)
    assert output.getexif().get(274) is None
    assert output.info.get("icc_profile") == profile
