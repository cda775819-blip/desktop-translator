# -*- coding: utf-8 -*-
"""End-to-end test of Engine.translate with chunking, progress and cancel."""
import importlib.util
import io
import re
import sys
import threading
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 路径解析见 _paths.py（本仓库不假设固定的绝对路径）
import _paths
app = _paths.load_app("ta")
E = app.ENGINE

failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(label)


PROSE = [
    "The old lighthouse had stood on the cliff for one hundred and twenty years.",
    "Every evening its beam swept across the water like a slow, patient hand.",
    "Sailors said they could see it from thirty miles out on a clear night.",
    "When the storm came in October, the keeper refused to leave his post.",
    "Waves broke over the rocks below with a sound like distant thunder.",
    "He wrote three letters that night, though he never posted any of them.",
    "By morning the wind had dropped and the sea was almost flat and grey.",
    "A fishing boat appeared at noon, drifting without lights or crew.",
    "The keeper rowed out to it and found nothing but a broken compass.",
    "He kept the compass on his windowsill for the rest of his life.",
]
LONG = " ".join((PROSE * 6)[:60])       # 60 句
print(f"测试文本: {len(LONG)} 字符, {LONG.count('.')} 句")

print("\n=== 1) 短文本（单块）===")
t0 = time.time()
out = E.translate("Good morning! How are you today?", "en", "zh")
print(f"  {time.time()-t0:.1f}s -> {out}")
check("短文本有输出", bool(out.strip()))

print("\n=== 2) 长文本 + 进度回调 ===")
msgs = []
t0 = time.time()
out_long = E.translate(LONG, "en", "zh", lambda m: msgs.append(m))
dt = time.time() - t0
print(f"  {len(LONG)} 字符 -> {len(out_long)} 字符, {dt:.1f}s "
      f"({len(LONG)/dt:.0f} 字符/秒)")
print(f"  进度消息数: {len(msgs)}, 样例: {msgs[:3]}")
check("长文本有输出", len(out_long) > 100)
check("有进度回调", len(msgs) > 0, f"{len(msgs)} 条")
check("进度格式正确", all("翻译中…" in m for m in msgs) if msgs else False)
check("未触发退化", not app.is_degenerate(out_long))
# 内容完整性：输出句子数量应与输入相当
in_sent = len([x for x in re.split(r"[.!?]+", LONG) if x.strip()])
out_sent = len([x for x in re.split(r"[。！？]+", out_long) if x.strip()])
print(f"  输入 {in_sent} 句 / 输出 {out_sent} 句")
# 注意：这里用的是重复文本，句数比没有意义（中文会合并同义短句）。
# 真正的保真度用不重复文本在 test_fidelity.py 里验证。
print(f"  句数比 {out_sent}/{in_sent}（重复文本，仅记录不判定）")

print("\n=== 3) 取消 ===")
ev = threading.Event()
ev.set()                      # 一开头就取消
t0 = time.time()
try:
    E.translate(LONG, "en", "zh", None, ev)
    check("取消应抛异常", False)
except app.TranslationCancelled:
    check("取消抛 TranslationCancelled", True, f"{time.time()-t0:.2f}s 内返回")
except Exception as exc:
    check("取消抛 TranslationCancelled", False, f"实际 {type(exc).__name__}")

print("\n=== 4) 中途取消 ===")
ev2 = threading.Event()
hit = {"n": 0}


def prog(m):
    hit["n"] += 1
    if hit["n"] >= 2:
        ev2.set()


t0 = time.time()
try:
    E.translate(LONG, "en", "zh", prog, ev2)
    check("中途取消应抛异常", False)
except app.TranslationCancelled:
    check("中途取消生效", True, f"{hit['n']} 条进度后, {time.time()-t0:.1f}s")
except Exception as exc:
    check("中途取消生效", False, f"{type(exc).__name__}: {exc}")

print("\n=== 5) 边界输入 ===")
for label, txt, src, tgt in (
    ("空字符串", "", "en", "zh"),
    ("纯空白", "   \n  ", "en", "zh"),
    ("单个词", "Hello", "en", "zh"),
    ("同语言", "Hello there", "en", "en"),
    ("纯数字", "12345", "en", "zh"),
    ("纯标点", "...!!!???", "en", "zh"),
):
    try:
        r = E.translate(txt, src, tgt)
        print(f"  ok   {label:<10} -> {r!r}")
        if label == "空字符串":
            check("空输入返回空", r == "")
        if label == "纯空白":
            check("空白输入返回空", r == "")
        if label == "同语言":
            check("同语言原样返回", r == txt)
    except Exception as exc:
        print(f"  FAIL {label:<10} {type(exc).__name__}: {exc}")
        failures.append(f"边界输入 {label}")

print("\n=== 6) 中日韩 ⇄ 英文 ===")
for src, tgt, txt in (("zh", "en", "这是一个测试。第二句话。第三句话。"),
                      ("ja", "zh", "これはテストです。二番目の文です。"),
                      ("en", "ja", "This is a test. Here is another sentence.")):
    r = E.translate(txt, src, tgt)
    print(f"  {src}->{tgt}: {r}")
    check(f"{src}->{tgt} 有输出", bool(r.strip()))

print("\n" + "=" * 58)
if failures:
    print(f"FAILURES ({len(failures)}):")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("引擎链路测试全部通过")
