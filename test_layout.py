# -*- coding: utf-8 -*-
"""Verify the window always opens fully on-screen, for a range of screen sizes
and DPI scales, and that the two-column layout stays usable."""
import importlib.util
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 路径解析见 _paths.py（本仓库不假设固定的绝对路径）
import _paths
app = _paths.load_app("ta")

failures = []


def simulate(phys_w, phys_h, scale, saved_geometry):
    """Replay TranslateApp's sizing maths for a given display, then build the
    real UI and confirm nothing is clipped."""
    import re
    aware = app._DPI_AWARE_OK
    if aware and scale > 1.0:
        sw, sh = max(640, int(phys_w / scale)), max(480, int(phys_h / scale))
    else:
        sw, sh = phys_w, phys_h

    win_w = max(760, min(1280, int(sw * 0.86)))
    win_h = max(520, min(820, int((sh - 60) * 0.92)))
    gw, gh = win_w, win_h
    if saved_geometry:
        m = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", saved_geometry)
        if m:
            a, b, c, d = (int(v) for v in m.groups())
            gw = max(760, min(a, sw - 40))
            gh = max(520, min(b, sh - 60))

    fits = win_w <= sw and win_h <= sh
    clamped_fits = gw <= sw and gh <= sh
    label = f"{phys_w}x{phys_h}@{scale:.2f}"
    print(f"\n  {label:<18} 逻辑 {sw}x{sh}")
    print(f"    默认窗口 {win_w}x{win_h}  放入屏幕: {'ok' if fits else '超出!'}")
    if saved_geometry:
        print(f"    保存 {saved_geometry} -> 夹取后 {gw}x{gh}  "
              f"{'ok' if clamped_fits else '仍超出!'}")
    if not fits:
        failures.append(f"{label} 默认窗口超出屏幕")
    if saved_geometry and not clamped_fits:
        failures.append(f"{label} 夹取后仍超出")
    return sw, sh


print("=== 各种屏幕/缩放下的窗口尺寸 ===")
for pw, ph, sc in ((2560, 1440, 1.5), (1920, 1080, 1.0), (1920, 1080, 1.25),
                   (1366, 768, 1.0), (3840, 2160, 2.0), (1280, 800, 1.0),
                   (2560, 1440, 1.0), (1707, 960, 1.0)):
    simulate(pw, ph, sc, "1801x700+601+523")   # the geometry that caused the bug

print("\n=== 真实构建：当前屏幕下的双栏布局 ===")
a = app.TranslateApp()
a.root.deiconify()
a.root.update_idletasks()
a.root.update()
sw, sh, sc = app._screen_logical_size(a.root)
w, h, x, y = (a.root.winfo_width(), a.root.winfo_height(),
              a.root.winfo_x(), a.root.winfo_y())
print(f"  逻辑屏幕 {sw}x{sh} (scale {sc})")
print(f"  窗口 {w}x{h}+{x}+{y}")
if x < 0 or y < 0 or x + w > sw or y + h > sh:
    failures.append("实际窗口超出逻辑屏幕")
    print("  FAIL 窗口超出屏幕")
else:
    print("  PASS 窗口完全在屏幕内")

# 两张卡片都要在窗口内，且有可用宽度
body = [c for c in a.root.winfo_children()
        if len(c.winfo_children()) == 2
        and all(k.winfo_class() == "Frame" for k in c.winfo_children())
        and c.winfo_y() > 50]
if not body:
    failures.append("找不到双栏容器")
else:
    left, right = body[0].winfo_children()
    for name, card in (("输入卡", left), ("输出卡", right)):
        r_edge = card.winfo_x() + card.winfo_width()
        inside = card.winfo_x() >= 0 and r_edge <= a.root.winfo_width() + 1
        print(f"  {name}: x={card.winfo_x()} w={card.winfo_width()} "
              f"右边缘={r_edge} -> {'在窗口内' if inside else '被裁!'}")
        if not inside:
            failures.append(f"{name}被裁切")
    print(f"  输入框可用宽度 {a.input_text.winfo_width()}px, "
          f"输出框 {a.output_text.winfo_width()}px")
    if a.input_text.winfo_width() < 200 or a.output_text.winfo_width() < 200:
        failures.append("文本框过窄")

# header 子控件不得互相压盖（用真实几何判断，pack 顺序不可靠）
def rect(w):
    return (w.winfo_rootx(), w.winfo_rooty(),
            w.winfo_rootx() + w.winfo_width(),
            w.winfo_rooty() + w.winfo_height())


def overlaps(r1, r2, slack=2):
    return not (r1[2] - slack <= r2[0] or r2[2] - slack <= r1[0]
                or r1[3] - slack <= r2[1] or r2[3] - slack <= r1[1])


header = a.root.winfo_children()[0]
# 只比较实际可见的叶子控件，容器互相嵌套是正常的
leaves = []


def collect(w):
    kids = [k for k in w.winfo_children() if k.winfo_ismapped()]
    if not kids:
        if w.winfo_width() > 0 and w.winfo_height() > 0:
            leaves.append(w)
        return
    for k in kids:
        collect(k)


for child in header.winfo_children():
    if child.winfo_ismapped():
        collect(child)

bad = []
for i in range(len(leaves)):
    for j in range(i + 1, len(leaves)):
        if overlaps(rect(leaves[i]), rect(leaves[j])):
            bad.append((leaves[i].winfo_class(), leaves[j].winfo_class()))
print(f"  header 可见控件 {len(leaves)} 个, 几何重叠: {bad or '无'}")
if bad:
    failures.append(f"header 控件重叠 {bad[:3]}")

print(f"  header 是否折行: {getattr(a, '_header_stacked', None)}")

a.root.destroy()

print("\n" + "=" * 58)
if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("布局测试全部通过")
