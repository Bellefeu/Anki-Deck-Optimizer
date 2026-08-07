#!/usr/bin/env python3
"""Draw the PRISM application icon, using nothing but the standard library.

The repository keeps no binary art. The icon is the same twenty two ruled
lines that draw the prism mark in the dashboard, read straight out of
``control_center/static/index.html`` so the two can never drift apart, and
rendered here as an additive point-and-glow field on black: the same grammar
as the backdrop, not an outline of it.

Two masters are drawn. The large one keeps every ruling, because at 128px and
up the strand structure is what the mark is. Below that the rulings collapse
into grey mush, so a second master draws a handful of thicker strands that
still read as a prism at 16px. Every delivered size is area averaged down
from whichever master suits it.

    python3 appbuild/icons.py build/icons
"""

from __future__ import annotations

import argparse
import math
import re
import struct
import sys
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARK_SOURCE = ROOT / "control_center/static/index.html"

# One master per legibility band. Twenty two rulings are the mark, but they
# only survive down to about 128px; below that the crossings merge into a
# single grey lozenge, so each band drops rulings and thickens the ones left
# until the silhouette is what carries the shape.
#   (master pixels, rulings kept, sigma as a fraction, gain, coverage, largest delivered size)
BANDS = (
    (128, 5, 0.048, 12.0, 0.88, 24),
    (256, 9, 0.020, 5.6, 0.82, 64),
    (1024, 0, 0.0030, 2.30, 0.72, 1024),
)

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PNG_SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)

# Apple draws a macOS icon inside a squircle that leaves a margin on the
# 1024 canvas. Windows and Linux show the artwork nearly full bleed.
MACOS_INSET = 0.098
FLAT_INSET = 0.035
SQUIRCLE_EXPONENT = 5.0

# Cold cobalt climbing to white, the dashboard's own ramp. Stops are
# (position, r, g, b) on a black ground.
RAMP = (
    (0.00, 0x00, 0x00, 0x00),
    (0.16, 0x0A, 0x18, 0x44),
    (0.42, 0x1C, 0x4C, 0xC8),
    (0.72, 0x7E, 0xB2, 0xFF),
    (1.00, 0xFF, 0xFF, 0xFF),
)


class IconError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# The mark


def read_rulings(source=MARK_SOURCE):
    """Return the prism rulings as (x0, y0, x1, y1) in a 0..1 square."""
    try:
        markup = Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        raise IconError(f"Cannot read the prism mark from {source}: {exc}") from exc
    match = re.search(r'class="mark-rulings"\s+d="([^"]+)"', markup)
    if not match:
        raise IconError(f"No element with class mark-rulings carries a path in {source}")
    pairs = re.findall(
        r"M\s*(-?[\d.]+)[ ,]+(-?[\d.]+)\s*L\s*(-?[\d.]+)[ ,]+(-?[\d.]+)",
        match.group(1),
    )
    if not pairs:
        raise IconError("The prism mark path carried no move-then-line segments")
    view = re.search(r'viewBox="0 0 (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)"', markup)
    span = float(view.group(1)) if view else 44.0
    return [tuple(float(value) / span for value in pair) for pair in pairs]


def thin(rulings, keep):
    """Evenly sample ``keep`` rulings, always including both outer edges."""
    if keep >= len(rulings):
        return list(rulings)
    last = len(rulings) - 1
    picked = {round(index * last / (keep - 1)) for index in range(keep)}
    return [rulings[index] for index in sorted(picked)]


def recentre(rulings, coverage):
    """Scale the rulings about their own centre to fill ``coverage`` of the art."""
    xs = [value for line in rulings for value in (line[0], line[2])]
    ys = [value for line in rulings for value in (line[1], line[3])]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    if width <= 0 or height <= 0:
        raise IconError("The prism mark has no extent to scale")
    scale = coverage / max(width, height)
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    return [
        (
            0.5 + (line[0] - cx) * scale, 0.5 + (line[1] - cy) * scale,
            0.5 + (line[2] - cx) * scale, 0.5 + (line[3] - cy) * scale,
        )
        for line in rulings
    ]


# --------------------------------------------------------------------------
# Rasterising


def gaussian_kernel(sigma):
    """A normalised square kernel, returned as (radius, flat row-major list)."""
    radius = max(1, int(math.ceil(sigma * 2.6)))
    span = radius * 2 + 1
    values = []
    for row in range(span):
        for column in range(span):
            dy, dx = row - radius, column - radius
            values.append(math.exp(-(dx * dx + dy * dy) / (2.0 * sigma * sigma)))
    total = sum(values)
    return radius, [value / total for value in values]


def stamp_strands(size, rulings, sigma, gain):
    """Accumulate a luminance field by walking a soft kernel along each line."""
    radius, kernel = gaussian_kernel(sigma)
    span = radius * 2 + 1
    field = [0.0] * (size * size)
    for x0, y0, x1, y1 in rulings:
        ax, ay = x0 * size, y0 * size
        bx, by = x1 * size, y1 * size
        length = math.hypot(bx - ax, by - ay)
        steps = max(2, int(length * 2.0))
        # Energy per unit length, so a long ruling is no brighter than a
        # short one; density comes from crossings, not from arc length.
        weight = gain * length / steps
        for step in range(steps + 1):
            t = step / steps
            px, py = ax + (bx - ax) * t, ay + (by - ay) * t
            ix, iy = int(px), int(py)
            for row in range(span):
                y = iy + row - radius
                if y < 0 or y >= size:
                    continue
                base = y * size
                krow = row * span
                for column in range(span):
                    x = ix + column - radius
                    if x < 0 or x >= size:
                        continue
                    field[base + x] += kernel[krow + column] * weight
    return field


def squircle_coverage(size, inset, exponent=SQUIRCLE_EXPONENT):
    """Antialiased superellipse coverage, computed a scanline at a time.

    The shape is closed form, so each sub scanline gives an exact horizontal
    span rather than a sampled one. Only the two boundary pixels of a span
    ever hold a fraction, which is both faster and cleaner than jittering.
    """
    coverage = [0.0] * (size * size)
    half = size / 2.0
    reach = half * (1.0 - 2.0 * inset)
    if reach <= 0:
        raise IconError("The icon inset leaves no room for artwork")
    slices = 4
    share = 1.0 / slices
    for row in range(size):
        base = row * size
        for slice_index in range(slices):
            cy = row + (slice_index + 0.5) / slices
            ratio = abs(cy - half) / reach
            if ratio >= 1.0:
                continue
            extent = reach * (1.0 - ratio ** exponent) ** (1.0 / exponent)
            left, right = half - extent, half + extent
            first, last = int(math.floor(left)), int(math.ceil(right)) - 1
            for x in range(max(0, first), min(size - 1, last) + 1):
                overlap = min(right, x + 1.0) - max(left, float(x))
                if overlap > 0:
                    coverage[base + x] += overlap * share
    return [min(1.0, value) for value in coverage]


def ramp_colour(level):
    level = 0.0 if level < 0.0 else (1.0 if level > 1.0 else level)
    for index in range(len(RAMP) - 1):
        low, high = RAMP[index], RAMP[index + 1]
        if level <= high[0]:
            reach = high[0] - low[0]
            t = 0.0 if reach <= 0 else (level - low[0]) / reach
            return tuple(int(round(low[1 + c] + (high[1 + c] - low[1 + c]) * t)) for c in range(3))
    return RAMP[-1][1:]


def render_master(size, rulings, *, sigma, gain, coverage, inset):
    """One square RGBA master: strands over a black squircle."""
    field = stamp_strands(size, recentre(rulings, coverage), sigma, gain)
    mask = squircle_coverage(size, inset)
    pixels = bytearray(size * size * 4)
    for index in range(size * size):
        alpha = mask[index]
        if alpha <= 0.0:
            continue
        # Additive exposure, the way the backdrop accumulates its points:
        # a lone ruling stays cobalt and only a crossing burns to white.
        red, green, blue = ramp_colour(1.0 - math.exp(-field[index] * 1.35))
        out = index * 4
        pixels[out] = red
        pixels[out + 1] = green
        pixels[out + 2] = blue
        pixels[out + 3] = int(round(alpha * 255))
    return bytes(pixels)


def downsample(pixels, source_size, target_size):
    """Box filter with premultiplied alpha, so edges do not fringe black."""
    if target_size == source_size:
        return pixels
    if target_size > source_size:
        raise IconError("Icons are only ever reduced, never enlarged")
    out = bytearray(target_size * target_size * 4)
    scale = source_size / target_size
    for ty in range(target_size):
        y0, y1 = int(ty * scale), int((ty + 1) * scale)
        y1 = max(y1, y0 + 1)
        for tx in range(target_size):
            x0, x1 = int(tx * scale), int((tx + 1) * scale)
            x1 = max(x1, x0 + 1)
            red = green = blue = alpha = 0.0
            count = 0
            for y in range(y0, y1):
                base = y * source_size
                for x in range(x0, x1):
                    offset = (base + x) * 4
                    a = pixels[offset + 3] / 255.0
                    red += pixels[offset] * a
                    green += pixels[offset + 1] * a
                    blue += pixels[offset + 2] * a
                    alpha += a
                    count += 1
            index = (ty * target_size + tx) * 4
            if alpha <= 0.0 or count == 0:
                continue
            out[index] = min(255, int(round(red / alpha)))
            out[index + 1] = min(255, int(round(green / alpha)))
            out[index + 2] = min(255, int(round(blue / alpha)))
            out[index + 3] = min(255, int(round(alpha / count * 255)))
    return bytes(out)


# --------------------------------------------------------------------------
# Containers


def encode_png(size, pixels):
    if len(pixels) != size * size * 4:
        raise IconError("Pixel buffer does not match the declared size")
    stride = size * 4
    raw = b"".join(b"\x00" + pixels[row * stride:(row + 1) * stride] for row in range(size))

    def chunk(kind, payload):
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"".join((
        b"\x89PNG\r\n\x1a\n",
        chunk(b"IHDR", header),
        chunk(b"IDAT", zlib.compress(raw, 9)),
        chunk(b"IEND", b""),
    ))


def encode_ico(images):
    """Windows icon holding PNG entries, which Vista and later all read."""
    entries, blobs = [], []
    offset = 6 + 16 * len(images)
    for size, blob in images:
        if not 1 <= size <= 256:
            raise IconError(f"An ICO entry must be 1 to 256 pixels, got {size}")
        entries.append(struct.pack(
            "<BBBBHHII",
            0 if size == 256 else size, 0 if size == 256 else size,
            0, 0, 1, 32, len(blob), offset,
        ))
        blobs.append(blob)
        offset += len(blob)
    return b"".join([struct.pack("<HHH", 0, 1, len(images))] + entries + blobs)


# Apple's PNG bearing types, and the pixel count each one must contain.
ICNS_TYPES = (
    (b"icp4", 16), (b"icp5", 32), (b"ic11", 32), (b"ic12", 64),
    (b"ic07", 128), (b"ic13", 256), (b"ic08", 256), (b"ic14", 512),
    (b"ic09", 512), (b"ic10", 1024),
)


def encode_icns(by_size):
    chunks = []
    for kind, size in ICNS_TYPES:
        blob = by_size.get(size)
        if blob is None:
            continue
        chunks.append(kind + struct.pack(">I", len(blob) + 8) + blob)
    if not chunks:
        raise IconError("No icon sizes were available for the macOS bundle")
    body = b"".join(chunks)
    return b"icns" + struct.pack(">I", len(body) + 8) + body


# --------------------------------------------------------------------------
# Delivery


def build_pngs(inset):
    """PNG bytes for every delivered size, keyed by pixel size."""
    rulings = read_rulings()
    masters = []
    for pixels, keep, sigma, gain, coverage, ceiling in BANDS:
        # Coarser bands lose the fine margin too, or the inset eats the art.
        band_inset = inset if ceiling > 64 else max(0.0, inset - 0.02)
        masters.append((ceiling, pixels, render_master(
            pixels, thin(rulings, keep) if keep else rulings,
            sigma=pixels * sigma, gain=gain, coverage=coverage, inset=band_inset,
        )))
    images = {}
    for size in PNG_SIZES:
        ceiling, pixels, master = next(band for band in masters if size <= band[0])
        images[size] = encode_png(size, downsample(master, pixels, size))
    return images


def write_icons(destination, *, quiet=False):
    """Write PRISM.icns, PRISM.ico and the loose PNGs into ``destination``."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    macos = build_pngs(MACOS_INSET)
    flat = build_pngs(FLAT_INSET)

    written = []

    def put(name, payload):
        path = destination / name
        path.write_bytes(payload)
        written.append(path)
        if not quiet:
            print(f"  {path.name}  {len(payload):,} bytes")

    put("PRISM.icns", encode_icns(macos))
    put("PRISM.ico", encode_ico([(size, flat[size]) for size in ICO_SIZES]))
    for size in PNG_SIZES:
        put(f"prism-{size}.png", flat[size])
    put("prism-macos-1024.png", macos[1024])
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", default=str(ROOT / "build/icons"))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    if not args.quiet:
        print(f"Drawing the PRISM icon into {args.destination}")
    write_icons(args.destination, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
