# -*- coding: utf-8 -*-
"""Generate the application icon: rounded plate, indigo->cyan gradient, 译 glyph.

Renders each size natively instead of downscaling a large render -- downscaling
blurs the thin strokes of 译 at 16/24/32 px, which is what makes taskbar icons
look muddy.

Requires Pillow (build-time only; it is NOT bundled into the app).

Usage:
    python gen_icon2.py [output.ico]
    # default: ./assets/app.ico next to this script
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

DEFAULT_ICO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "assets", "app.ico")
OUT_ICO = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ICO
OUT_PNG = os.path.splitext(OUT_ICO)[0] + "_preview.png"

C_TOP = (99, 102, 241)        # indigo 500
C_BOT = (34, 211, 238)        # cyan 400
# 小尺寸下渐变过渡到亮青色会让白笔画对比不足，换一档更深的青
C_BOT_SMALL = (14, 148, 190)  # deeper cyan, used for <= 24px
GLYPH = "\u8bd1"              # 译
FONTS = [
    r"C:\Windows\Fonts\msyhbd.ttc",     # Microsoft YaHei Bold
    r"C:\Windows\Fonts\msyh.ttc",       # Microsoft YaHei
    r"C:\Windows\Fonts\simhei.ttf",     # SimHei
]
SIZES = [256, 128, 64, 48, 32, 24, 16]
SS = 3                                   # supersample factor (AA only)


def gradient(size: int, c_bot=None) -> Image.Image:
    c_bot = c_bot or C_BOT
    ramp = Image.new("L", (size * 2, 1))
    rp = ramp.load()
    for i in range(size * 2):
        rp[i, 0] = int(round(255 * i / (size * 2 - 1)))
    ramp = ramp.resize((size, size), Image.BILINEAR)
    rp = ramp.load()
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = rp[x, y] / 255.0
            px[x, y] = tuple(int(round(a + (b - a) * t))
                             for a, b in zip(C_TOP, c_bot))
    return img


def font_for(px: int) -> ImageFont.FreeTypeFont:
    for path in FONTS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, px)
            except OSError:
                continue
    return ImageFont.load_default()


def render(size: int) -> Image.Image:
    """Render one icon at exactly `size` px, supersampled SS times."""
    big = size * SS
    pad = big * 0.045
    plate = (pad, pad, big - pad, big - pad)
    radius = big * 0.215

    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(plate, radius=radius, fill=255)

    small = size <= 24
    img = gradient(big, C_BOT_SMALL if small else None).convert("RGBA")

    # 顶部柔光，向下淡出
    fade = Image.new("L", (big, big))
    fp = fade.load()
    for y in range(big):
        v = max(0.0, 1.0 - y / (big * 0.62))
        row = int(40 * v * v * 0.16)
        for x in range(big):
            fp[x, y] = row
    white = Image.new("RGBA", (big, big), (255, 255, 255, 0))
    white.putalpha(fade)
    img = Image.alpha_composite(img, white)
    img.putalpha(mask)

    # 字形：小尺寸放大占位，让笔画落在整数像素上
    share = 0.76 if size <= 16 else (0.72 if size <= 32 else 0.66)
    font = font_for(int(big * share))
    canvas = Image.new("L", (big, big), 0)
    cd = ImageDraw.Draw(canvas)
    bbox = cd.textbbox((0, 0), GLYPH, font=font)
    gw, gh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    cd.text(((big - gw) / 2 - bbox[0], (big - gh) / 2 - bbox[1]),
            GLYPH, font=font, fill=255)

    # 小尺寸把半透明边缘拉实，避免白字和底板糊在一起。
    # 注意：不要用 MaxFilter 加粗，那会让笔画胀成一团，实测锐度反而下降。
    if small:
        canvas = canvas.point(lambda v: 255 if v > 96 else 0)

    # 投影
    off = max(2, int(big * 0.020))
    shadow = Image.new("L", (big, big), 0)
    shadow.paste(canvas, (off, off))
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(1, big * 0.012)))
    shadow = shadow.point(lambda v: int(v * 0.34))
    img = Image.alpha_composite(
        img, Image.merge("RGBA", (Image.new("L", (big, big), 0),) * 3 + (shadow,)))

    layer = Image.new("RGBA", (big, big), (255, 255, 255, 0))
    layer.putalpha(canvas)
    img = Image.alpha_composite(img, layer)

    # 裁到圆角内，避免投影溢出
    clipped = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    clipped.paste(img, (0, 0), mask)
    return clipped.resize((size, size), Image.LANCZOS)


def sharpness(img: Image.Image) -> tuple[float, int]:
    """平均拉普拉斯绝对值（越大边缘越锐）和近白像素数。"""
    g = img.convert("L")
    w, h = g.size
    px = g.load()
    total = 0
    n = 0
    glyph_px = 0
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            total += abs(4 * px[x, y] - px[x - 1, y] - px[x + 1, y]
                         - px[x, y - 1] - px[x, y + 1])
            n += 1
            if px[x, y] > 200:
                glyph_px += 1
    return total / max(1, n), glyph_px


def main() -> int:
    os.makedirs(os.path.dirname(OUT_ICO), exist_ok=True)
    frames = {}
    print(f"{'size':>5} {'laplacian':>10} {'glyph px':>9}  {'share':>6}")
    for size in SIZES:
        f = render(size)
        frames[size] = f
        lap, gpx = sharpness(f)
        print(f"{size:>5} {lap:>10.1f} {gpx:>9}  {gpx/(size*size):>5.1%}")

    # Pillow writes each supplied image at its native size
    frames[256].save(OUT_ICO, format="ICO",
                     sizes=[(s, s) for s in SIZES],
                     append_images=[frames[s] for s in SIZES if s != 256])
    frames[256].save(OUT_PNG, format="PNG")
    print(f"\nwrote {OUT_ICO} ({os.path.getsize(OUT_ICO)} bytes)")
    print(f"wrote {OUT_PNG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
