# -*- coding: utf-8 -*-
"""Enumerate RT_ICON / RT_GROUP_ICON resources inside a built exe.

Proves the multi-resolution icon (not just a single frame) made it into the PE,
which is what Windows uses to pick a crisp size for the taskbar / Explorer.

Usage: python verify_exe_icon.py [path-to.exe]
"""
import io
import os
import struct
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import _paths

EXE = sys.argv[1] if len(sys.argv) > 1 else (_paths.find_exe() or "")
if not EXE or not os.path.isfile(EXE):
    print("找不到 exe。用法: python verify_exe_icon.py <exe 路径>")
    sys.exit(2)

data = open(EXE, "rb").read()
print(f"exe: {EXE}  ({len(data)} bytes)")

assert data[:2] == b"MZ", "not a PE file"
pe_off = struct.unpack_from("<I", data, 0x3C)[0]
assert data[pe_off:pe_off + 4] == b"PE\0\0", "bad PE signature"

machine, nsec = struct.unpack_from("<HH", data, pe_off + 4)
opt_size = struct.unpack_from("<H", data, pe_off + 20)[0]
opt_off = pe_off + 24
magic = struct.unpack_from("<H", data, opt_off)[0]
print(f"machine=0x{machine:04X}  sections={nsec}  optional magic=0x{magic:04X} "
      f"({'PE32+' if magic == 0x20B else 'PE32'})")

# data directories start at +112 for PE32+, +96 for PE32
dd_off = opt_off + (112 if magic == 0x20B else 96)
res_rva, res_size = struct.unpack_from("<II", data, dd_off + 2 * 8)
print(f"resource directory: RVA=0x{res_rva:X} size={res_size}")

secs = []
sec_off = opt_off + opt_size
for i in range(nsec):
    o = sec_off + 40 * i
    name = data[o:o + 8].rstrip(b"\0").decode("ascii", "replace")
    vsize, vaddr, rsize, raw = struct.unpack_from("<IIII", data, o + 8)
    secs.append((name, vaddr, vsize, raw, rsize))
    print(f"  section {name:<8} VA=0x{vaddr:<8X} vsize={vsize:<8} raw={raw}")


def rva_to_off(rva: int) -> int:
    for _n, va, vsz, raw, rsz in secs:
        if va <= rva < va + max(vsz, rsz):
            return raw + (rva - va)
    raise ValueError(f"RVA 0x{rva:X} not in any section")


# Resource directory offsets below the top level are relative to the resource
# directory base, so remember it and walk with absolute file offsets.
res_base_off = rva_to_off(res_rva)
entries = []


def walk(dir_off: int, depth: int, path: tuple):
    n_named, n_id = struct.unpack_from("<HH", data, dir_off + 12)
    for i in range(n_named + n_id):
        e = dir_off + 16 + 8 * i
        name_id, offset = struct.unpack_from("<II", data, e)
        if offset & 0x80000000:
            walk(res_base_off + (offset & 0x7FFFFFFF), depth + 1, path + (name_id,))
        else:
            data_rva, size, _cp, _res = struct.unpack_from(
                "<IIII", data, res_base_off + offset)
            entries.append((path + (name_id,), data_rva, size))


walk(res_base_off, 0, ())

RT_ICON, RT_GROUP_ICON = 3, 14
icons = [e for e in entries if e[0] and e[0][0] == RT_ICON]
groups = [e for e in entries if e[0] and e[0][0] == RT_GROUP_ICON]
print(f"\nRT_ICON entries      : {len(icons)}")
print(f"RT_GROUP_ICON entries: {len(groups)}")

WANT = (256, 128, 64, 48, 32, 24, 16)
ok_all = bool(groups)

for path, rva, size in groups:
    off = rva_to_off(rva)
    count = struct.unpack_from("<H", data, off + 4)[0]
    need = 6 + 14 * count          # GRPICONDIRENTRY is 14 bytes
    blob = data[off:off + max(size, need)]
    if len(blob) < need:
        print(f"  !! group {path[1:]}: only {len(blob)} bytes, need {need}")
        ok_all = False
        continue
    reserved, itype, count = struct.unpack_from("<HHH", blob, 0)
    print(f"\n  group {path[1:]}  reserved={reserved} type={itype} frames={count}")
    sizes = []
    for i in range(count):
        w, h, colors, res, planes, bpp, isize, iid = struct.unpack_from(
            "<BBBBHHIH", blob, 6 + 14 * i)
        sizes.append((w or 256, bpp, isize))
        print(f"    frame {i}: {w or 256:>3}x{h or 256:<3} {bpp}bpp "
              f"{isize:>7} bytes  id={iid}")

    got = tuple(sorted((s[0] for s in sizes), reverse=True))
    print(f"    sizes present: {got}")
    print(f"    {'PASS' if got == WANT else 'FAIL'}: expected {WANT}")
    if got != WANT:
        ok_all = False

print()
if len(icons) >= 7 and ok_all:
    print("EXE ICON VERIFIED: multi-resolution icon embedded in the PE resources")
else:
    print("FAILED: icon resources missing or wrong sizes")
    sys.exit(1)
