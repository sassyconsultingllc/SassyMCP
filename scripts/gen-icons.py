# Copyright (c) 2026 Shane Smith / Sassy Consulting LLC. All rights reserved.
# Proprietary source. This notice is Copyright Management Information (17 U.S.C. 1202); removal or alteration prohibited.
# CodeMark: SCLLC1-SassyMCP-S4UU5R67DNWB
"""Generate icon.png files for the VS Code extension and MCPB package.

Pure-PIL gradient + bold "S" — matches the gradient in resources/icon.svg.
192x192 is the standard size for both VS Code marketplace icons and MCPB
package icons.

Run from the repo root:
    python scripts/gen-icons.py

Output:
    sassymcp-vscode/resources/icon.png
    mcpb/icon.png
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _gradient_background(size: int) -> Image.Image:
    """Diagonal purple-to-pink gradient matching the SVG's #7c3aed -> #ec4899."""
    img = Image.new("RGB", (size, size))
    pixels = img.load()
    assert pixels is not None
    start = (0x7C, 0x3A, 0xED)  # purple
    end = (0xEC, 0x38, 0x99)    # pink
    for y in range(size):
        for x in range(size):
            # Diagonal interpolation: 0..1 across the diagonal
            t = (x + y) / (2 * (size - 1))
            r = round(start[0] + (end[0] - start[0]) * t)
            g = round(start[1] + (end[1] - start[1]) * t)
            b = round(start[2] + (end[2] - start[2]) * t)
            pixels[x, y] = (r, g, b)
    return img


def _round_corners(img: Image.Image, radius: int) -> Image.Image:
    """Apply rounded corners via alpha mask."""
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, img.size[0] - 1, img.size[1] - 1), radius=radius, fill=255)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _draw_s(img: Image.Image, size: int) -> None:
    """Centered bold 'S' in white. Falls back through a font search list since
    we can't assume any specific font is installed on the build machine."""
    draw = ImageDraw.Draw(img)
    font_size = int(size * 0.625)
    font_candidates = [
        "arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf",
        "Helvetica-Bold.ttf", "LiberationSans-Bold.ttf",
    ]
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont
    for name in font_candidates:
        try:
            font = ImageFont.truetype(name, font_size)
            break
        except OSError:
            continue
    else:
        font = ImageFont.load_default()
    # Measure text and center
    bbox = draw.textbbox((0, 0), "S", font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1]
    # Slight downward nudge to optical-center the S (it has a heavier bottom curve)
    y -= int(size * 0.02)
    draw.text((x, y), "S", font=font, fill=(255, 255, 255, 255))


def make_icon(size: int = 192, corner_radius: int = 32) -> Image.Image:
    bg = _gradient_background(size)
    bg_rgba = bg.convert("RGBA")
    _draw_s(bg_rgba, size)
    return _round_corners(bg_rgba, corner_radius)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    targets = [
        repo_root / "sassymcp-vscode" / "resources" / "icon.png",
        repo_root / "mcpb" / "icon.png",
    ]
    icon = make_icon()
    for t in targets:
        t.parent.mkdir(parents=True, exist_ok=True)
        icon.save(t, format="PNG", optimize=True)
        print(f"Wrote {t} ({t.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
