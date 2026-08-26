"""Build the CHMI test fixtures. Usage: python scripts/make_chmi_fixtures.py

Two of the fixtures are **real, recorded frames** downloaded from
opendata.chmi.cz — an observed composite and one run's forecast tar — so the
adapter is tested against bytes the service actually produced, including its exact
palette, its 680x460 geometry and the grey domain outline drawn into it.

Two more are synthesized on the same geometry, because a recorded frame cannot
prove a negative: `frame_dry.png` has no echo anywhere, and `frame_elsewhere.png`
puts a storm over Praha and nothing else, which is what catches a projection that
is merely plausible instead of correct.

Re-recording the real pair is a deliberate act: the expected values in
`tests/test_chmi.py` are pinned to these bytes and must be recomputed with them.
"""

from __future__ import annotations

import math
import re
import urllib.request
from pathlib import Path

from PIL import Image

TARGET = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "chmi"
ROOT = "https://opendata.chmi.cz/meteorology/weather/radar/composite"
UA = "walk_the_dog/0.1.0 (+https://github.com/zyndata/walk-the-dog)"

# Mirrors custom_components/walk_the_dog/sources/chmi.py — kept local so the
# generator can be run without importing Home Assistant.
FRAME_WIDTH, FRAME_HEIGHT = 680, 460
IMAGE_WEST_LON, IMAGE_EAST_LON = 11.267, 20.770
IMAGE_SOUTH_LAT, IMAGE_NORTH_LAT = 48.047, 52.167

PALETTE = [
    (0x00, 0x00, 0x00),
    (0x38, 0x00, 0x70),
    (0x30, 0x00, 0xA8),
    (0x00, 0x00, 0xFC),
    (0x00, 0x6C, 0xC0),
    (0x00, 0xA0, 0x00),
    (0x00, 0xBC, 0x00),
    (0x34, 0xD8, 0x00),
    (0x9C, 0xDC, 0x00),
    (0xE0, 0xDC, 0x00),
    (0xFC, 0xB0, 0x00),
    (0xFC, 0x84, 0x00),
    (0xFC, 0x58, 0x00),
    (0xFC, 0x00, 0x00),
    (0xA0, 0x00, 0x00),
    (0xFC, 0xFC, 0xFC),
]

PRAHA = (50.0755, 14.4378)


def get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    print(f"  {response.status} {len(data):>8} B  {url}")
    return data


def newest(listing_url: str, pattern: str) -> str:
    names = sorted(set(re.findall(pattern, get(listing_url).decode("utf-8", "replace"))))
    if not names:
        raise RuntimeError(f"nothing matching {pattern} at {listing_url}")
    return names[-1]


def merc(lat: float) -> float:
    return math.asinh(math.tan(math.radians(lat)))


def to_pixel(lat: float, lon: float) -> tuple[float, float]:
    north = merc(IMAGE_NORTH_LAT)
    span = north - merc(IMAGE_SOUTH_LAT)
    return (
        (lon - IMAGE_WEST_LON) / (IMAGE_EAST_LON - IMAGE_WEST_LON) * FRAME_WIDTH,
        (north - merc(lat)) / span * FRAME_HEIGHT,
    )


def blank() -> Image.Image:
    image = Image.new("P", (FRAME_WIDTH, FRAME_HEIGHT), 0)
    flat: list[int] = []
    for rgb in PALETTE:
        flat.extend(rgb)
    flat.extend([0] * (768 - len(flat)))
    image.putpalette(flat)
    return image


def disc(image: Image.Image, lat: float, lon: float, radius_px: float, level: int) -> None:
    cx, cy = to_pixel(lat, lon)
    pixels = image.load()
    for y in range(max(0, int(cy - radius_px)), min(FRAME_HEIGHT, int(cy + radius_px) + 1)):
        for x in range(max(0, int(cx - radius_px)), min(FRAME_WIDTH, int(cx + radius_px) + 1)):
            if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= radius_px**2:
                pixels[x, y] = level


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)

    print("recorded frames from opendata.chmi.cz:")
    observed_name = newest(f"{ROOT}/maxz/png/", r'href="(pacz2gmaps3\.z_max3d\.[\d.]+\.png)"')
    (TARGET / "observed.png").write_bytes(get(f"{ROOT}/maxz/png/{observed_name}"))
    (TARGET / "observed.name").write_text(observed_name + "\n", encoding="utf-8")

    tar_name = newest(f"{ROOT}/fct_maxz/png/", r'href="(pacz2gmaps3\.fct_z_max\.[\w.]+\.tar)"')
    (TARGET / "forecast.tar").write_bytes(get(f"{ROOT}/fct_maxz/png/{tar_name}"))
    (TARGET / "forecast.name").write_text(tar_name + "\n", encoding="utf-8")

    print("\nsynthesized frames on the same geometry:")
    blank().save(TARGET / "frame_dry.png", format="PNG", transparency=0, optimize=True)
    print(f"  frame_dry.png       {(TARGET / 'frame_dry.png').stat().st_size} B")

    elsewhere = blank()
    disc(elsewhere, *PRAHA, radius_px=30.0, level=13)
    elsewhere.save(TARGET / "frame_elsewhere.png", format="PNG", transparency=0, optimize=True)
    print(f"  frame_elsewhere.png {(TARGET / 'frame_elsewhere.png').stat().st_size} B")

    print(f"\nrecorded run: observed {observed_name}, forecast {tar_name}")


if __name__ == "__main__":
    main()
