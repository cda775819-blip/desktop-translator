# -*- coding: utf-8 -*-
"""Structural verification of the generated icon, without needing to view it.

Checks, for every frame in the .ico:
  * the container header and payload offsets tile the file exactly
  * the plate is a rounded square (corners transparent)
  * the plate actually covers most of the canvas
  * a white glyph shape is present
Then reports the average plate colour and the gradient endpoints.

Usage: python verify_icon.py [app.ico]
"""
import io
import os
import struct
import sys
import zlib

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ICO = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "assets", "app.ico")
data = open(ICO, "rb").read()

reserved, itype, count = struct.unpack_from("<HHH", data, 0)
print(f"ICO header: reserved={reserved} type={itype} entries={count}")
assert reserved == 0 and itype == 1, "not a valid icon header"
assert count == 7, f"expected 7 entries, got {count}"

entries = []
for i in range(count):
    w, h, colors, res, planes, bpp, size, offset = struct.unpack_from(
        "<BBBBHHII", data, 6 + 16 * i)
    entries.append((w or 256, h or 256, size, offset, bpp))
    print(f"  entry {i}: {w or 256:>3}x{h or 256:<3} {bpp}bpp  {size:>7} bytes "
          f"@ {offset}")

# offsets/sizes must tile the file exactly
end = 6 + 16 * count
for size_px, _, size, offset, _ in entries:
    assert offset == end, f"entry {size_px} offset {offset} != {end}"
    end += size
assert end == len(data), f"trailing bytes: {end} vs {len(data)}"
print(f"\npayload layout consistent, file = {len(data)} bytes")


def decode_png(png: bytes):
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "bad png signature"
    pos = 8
    w = h = None
    idat = b""
    while pos < len(png):
        ln = struct.unpack_from(">I", png, pos)[0]
        tag = png[pos + 4:pos + 8]
        body = png[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack_from(">IIBB", body, 0)
            assert (depth, ctype) == (8, 6), f"want 8-bit RGBA, got {depth}/{ctype}"
        elif tag == b"IDAT":
            idat += body
        pos += 12 + ln
    raw = zlib.decompress(idat)

    stride = w * 4
    out = bytearray()
    prev = bytearray(stride)
    p = 0
    for _ in range(h):
        ft = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        if ft == 0:
            pass
        elif ft == 1:                       # Sub
            for i in range(4, stride):
                line[i] = (line[i] + line[i - 4]) & 0xFF
        elif ft == 2:                       # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:                       # Average
            for i in range(stride):
                a = line[i - 4] if i >= 4 else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:                       # Paeth
            for i in range(stride):
                a = line[i - 4] if i >= 4 else 0
                b = prev[i]
                c = prev[i - 4] if i >= 4 else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        else:
            raise AssertionError(f"unknown PNG filter {ft}")
        out += line
        prev = line
    return w, h, bytes(out)


def decode_bmp(bmp: bytes):
    (hs, w, h2, planes, bpp) = struct.unpack_from("<IiiHH", bmp, 0)
    h = h2 // 2
    assert (planes, bpp) == (1, 32), f"bad bmp header {planes}/{bpp}"
    px = bytearray(w * h * 4)
    off = hs
    for y in range(h):
        src = off + (h - 1 - y) * w * 4          # stored bottom-up
        for x in range(w):
            b, g, r, a = bmp[src + x * 4: src + x * 4 + 4]
            i = (y * w + x) * 4
            px[i:i + 4] = bytes((r, g, b, a))
    return w, h, bytes(px)


def report(w, h, rgba, label):
    def px(x, y):
        i = (y * w + x) * 4
        return rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]

    corners = [px(0, 0), px(w - 1, 0), px(0, h - 1), px(w - 1, h - 1)]
    centre = px(w // 2, h // 2)
    opaque = sum(1 for i in range(3, len(rgba), 4) if rgba[i] > 200)
    whites = sum(1 for i in range(0, len(rgba), 4)
                 if rgba[i] > 230 and rgba[i + 1] > 230 and rgba[i + 2] > 230
                 and rgba[i + 3] > 200)
    total = w * h

    ok_corner = all(c[3] < 16 for c in corners)          # rounded -> transparent
    ok_plate = opaque / total > 0.60
    ok_glyph = whites / total > 0.02

    print(f"\n{label}  {w}x{h}")
    print(f"  corner alpha     : {[c[3] for c in corners]}  -> "
          f"{'rounded (transparent)' if ok_corner else 'SQUARE (bad)'}")
    print(f"  opaque coverage  : {opaque/total:.1%}  -> "
          f"{'plate present' if ok_plate else 'TOO LITTLE (bad)'}")
    print(f"  white pixels     : {whites/total:.1%}  -> "
          f"{'glyph present' if ok_glyph else 'NO GLYPH (bad)'}")
    print(f"  centre pixel     : rgba{centre}")
    return ok_corner and ok_plate and ok_glyph


problems = []
for idx, (size_px, _, size, offset, _) in enumerate(entries):
    blob = data[offset:offset + size]
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        w, h, rgba = decode_png(blob)
        kind = "PNG"
    else:
        w, h, rgba = decode_bmp(blob)
        kind = "BMP"
    if (w, h) != (size_px, size_px):
        print(f"  !! entry {idx} header says {size_px} but image is {w}x{h}")
        problems.append(size_px)
    if not report(w, h, rgba, kind):
        problems.append(size_px)

print("\n" + "=" * 58)
if problems:
    print(f"PROBLEMS at sizes: {problems}")
    sys.exit(1)
print("ICON VERIFIED: rounded plate + white glyph at every size")

# colour probes on the largest frame
big_off = entries[-1][3]
big_len = entries[-1][2]
w, h, rgba = decode_png(data[big_off:big_off + big_len])
n = r = g = b = 0
for y in range(0, h, 4):
    for x in range(0, w, 4):
        i = (y * w + x) * 4
        if rgba[i + 3] > 200 and not (rgba[i] > 230 and rgba[i + 1] > 230):
            r += rgba[i]; g += rgba[i + 1]; b += rgba[i + 2]; n += 1
if n:
    print(f"plate average colour: #{r//n:02X}{g//n:02X}{b//n:02X} over {n} samples")
    for name, (x, y) in (("top-left", (20, 20)),
                         ("bottom-right", (w - 20, h - 20))):
        i = (y * w + x) * 4
        print(f"gradient {name:<13}: #{rgba[i]:02X}{rgba[i+1]:02X}{rgba[i+2]:02X} "
              f"alpha={rgba[i+3]}")
