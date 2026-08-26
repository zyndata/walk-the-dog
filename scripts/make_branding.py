"""Draw the brand images. Usage: python scripts/make_branding.py

The icon is a rain-blue badge with a white paw print under three falling drops —
"pet" and "rain" are the two things the integration is about, and both survive
being shrunk to the 24 px the frontend actually renders an integration icon at.
A dog silhouette does not survive that, which is why there is none.

Everything is drawn at four times the delivered size and reduced with LANCZOS:
that antialiases the badge corners and the paw edges far better than drawing them
at final size does, at no cost to a file that is generated once and committed.

Output goes to `branding/custom_integrations/walk_the_dog/`, which is exactly the
folder that gets copied into a home-assistant/brands pull request (phase 9).
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET = REPO_ROOT / "branding" / "custom_integrations" / "walk_the_dog"

#: Delivered icon sizes, fixed by home-assistant/brands: square, 256 and 512.
ICON_SIZE = 256
ICON_2X_SIZE = 512

#: Drawing happens this many times larger than the largest delivered image.
SUPERSAMPLE = 4

#: Badge gradient, top to bottom: a rain sky, not a clear one.
SKY_TOP = (0x53, 0xA8, 0xE8)
SKY_BOTTOM = (0x1C, 0x54, 0x8C)

#: The paw, the drops, and the wordmark on a light and on a dark background.
PAW = (0xFF, 0xFF, 0xFF, 0xFF)
DROP = (0xFF, 0xFF, 0xFF, 0xA8)
WORDMARK_LIGHT = (0x14, 0x38, 0x5C, 0xFF)
WORDMARK_DARK = (0xEA, 0xF2, 0xFA, 0xFF)

WORDMARK_TEXT = "Walk the dog"

#: DejaVu is the one family that is present on this project's two dev machines and
#: on the CI image, and its licence allows anything this script does with it.
FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    Path("/Library/Fonts/DejaVuSans-Bold.ttf"),
)


class Blob(NamedTuple):
    """One tilted ellipse, in fractions of the badge it is drawn on."""

    centre_x: float
    centre_y: float
    width: float
    height: float
    angle: float


#: The toes, from the outer left one round to the outer right one. Tilting the
#: outer pair outwards is what stops four ellipses in a row from reading as four
#: ellipses in a row.
TOES = (
    Blob(0.245, 0.475, 0.150, 0.190, 26.0),
    Blob(0.408, 0.392, 0.156, 0.200, 9.0),
    Blob(0.592, 0.392, 0.156, 0.200, -9.0),
    Blob(0.755, 0.475, 0.150, 0.190, -26.0),
)

#: The pad the toes sit above.
PAD = Blob(0.5, 0.700, 0.470, 0.360, 0.0)

#: Drops falling in the band above the paw.
DROPS = (
    Blob(0.205, 0.185, 0.050, 0.175, 15.0),
    Blob(0.500, 0.130, 0.054, 0.195, 15.0),
    Blob(0.795, 0.200, 0.047, 0.165, 15.0),
)


def _font(size: int) -> ImageFont.FreeTypeFont:
    """The wordmark face, or a clear error naming what to install."""
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            return ImageFont.truetype(candidate, size)
    searched = "\n  ".join(str(path) for path in FONT_CANDIDATES)
    message = f"DejaVu Sans Bold not found. Looked in:\n  {searched}"
    raise SystemExit(message)


def _gradient(size: int, radius: int) -> Image.Image:
    """The rounded badge, filled top to bottom with the rain-sky gradient."""
    columns = Image.new("RGB", (1, size))
    pixels = columns.load()
    assert pixels is not None
    for y in range(size):
        ratio = y / (size - 1)
        pixels[0, y] = tuple(
            round(top + (bottom - top) * ratio)
            for top, bottom in zip(SKY_TOP, SKY_BOTTOM, strict=True)
        )
    badge = columns.resize((size, size)).convert("RGBA")

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    badge.putalpha(mask)
    return badge


def _blob(size: int, blob: Blob, colour: tuple[int, int, int, int]) -> Image.Image:
    """One tilted ellipse — a toe, a pad or a drop — on its own transparent layer.

    Drawn upright and then rotated, because Pillow cannot draw a rotated ellipse
    and a polygon approximation would show its corners at this magnification.
    """
    box_w, box_h = round(blob.width * size), round(blob.height * size)
    layer = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse((0, 0, box_w - 1, box_h - 1), fill=colour)
    if blob.angle:
        layer = layer.rotate(blob.angle, resample=Image.Resampling.BICUBIC, expand=True)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(
        layer,
        (
            round(blob.centre_x * size - layer.width / 2),
            round(blob.centre_y * size - layer.height / 2),
        ),
    )
    return canvas


def _render_badge(canvas: int) -> Image.Image:
    """The badge at exactly `canvas` pixels square, with no reduction of its own.

    Kept separate from `draw_icon` so the logo can compose its badge at the size it
    is already supersampling to, instead of supersampling a supersample.
    """
    icon = _gradient(canvas, radius=round(canvas * 0.22))

    for drop in DROPS:
        icon.alpha_composite(_blob(canvas, drop, DROP))

    for shape in (PAD, *TOES):
        icon.alpha_composite(_blob(canvas, shape, PAW))

    return icon


def draw_icon(size: int) -> Image.Image:
    """The badge at `size` pixels square, supersampled and reduced."""
    icon = _render_badge(size * SUPERSAMPLE)
    return icon.resize((size, size), Image.Resampling.LANCZOS)


def draw_logo(height: int, colour: tuple[int, int, int, int]) -> Image.Image:
    """The badge with the wordmark beside it, trimmed to what it actually covers.

    The name in the logo is the untranslated one: `manifest.json` keeps "Walk the
    dog" in every language, and a brand image is not something Home Assistant can
    swap per language anyway.
    """
    canvas_h = height * SUPERSAMPLE
    badge = _render_badge(canvas_h)

    font = _font(round(canvas_h * 0.44))
    gap = round(canvas_h * 0.20)
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    left, top, right, bottom = measure.textbbox((0, 0), WORDMARK_TEXT, font=font)

    logo = Image.new("RGBA", (canvas_h + gap + (right - left), canvas_h), (0, 0, 0, 0))
    logo.alpha_composite(badge, (0, 0))
    ImageDraw.Draw(logo).text(
        (canvas_h + gap - left, canvas_h // 2 - (top + bottom) // 2),
        WORDMARK_TEXT,
        font=font,
        fill=colour,
    )

    box = logo.getbbox()
    assert box is not None
    logo = logo.crop(box)
    return logo.resize(
        (round(logo.width / SUPERSAMPLE), round(logo.height / SUPERSAMPLE)),
        Image.Resampling.LANCZOS,
    )


def _save(image: Image.Image, name: str) -> None:
    """Write one PNG, losslessly optimized, and say what it cost."""
    path = TARGET / name
    image.save(path, format="PNG", optimize=True)
    print(f"{path.relative_to(REPO_ROOT)}  {image.width}x{image.height}  {path.stat().st_size} B")


def main() -> None:
    """Draw every delivered image."""
    TARGET.mkdir(parents=True, exist_ok=True)
    _save(draw_icon(ICON_SIZE), "icon.png")
    _save(draw_icon(ICON_2X_SIZE), "icon@2x.png")
    for scale, suffix in ((ICON_SIZE, ""), (ICON_2X_SIZE, "@2x")):
        _save(draw_logo(scale, WORDMARK_LIGHT), f"logo{suffix}.png")
        _save(draw_logo(scale, WORDMARK_DARK), f"dark_logo{suffix}.png")


if __name__ == "__main__":
    main()
