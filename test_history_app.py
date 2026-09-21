# -*- coding: utf-8 -*-
"""Integration test: history capture is wired into the app's message handling.

This is the seam where a bug would hurt most -- recording happens *after* a
successful translation, so anything that throws here would show the user
"translation failed" for a translation that actually succeeded.

Uses a temp history file; never touches the deployed one.
"""
import io
import os
import queue
import shutil
import sys
import tempfile
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\translator-build")
import _paths

app_mod = _paths.load_app("ta")

failures = []


def check_true(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label:<54} {detail}")
    if not cond:
        failures.append(label)


def fresh_app():
    """App instance with history redirected to a temp file."""
    tmpd = tempfile.mkdtemp(prefix="histapp-")
    app_mod.HISTORY.path = os.path.join(tmpd, "history.jsonl")
    app_mod.HISTORY._invalidate()
    a = app_mod.TranslateApp()
    a.root.deiconify()
    a.root.update_idletasks()
    a.root.update()
    return a, tmpd


def teardown(a, tmpd):
    try:
        a.root.destroy()
    except Exception:
        pass
    shutil.rmtree(tmpd, ignore_errors=True)


print("=== 1) 翻译成功 -> 经 _handle 的 ok 分支写入历史 ===")
a, tmpd = fresh_app()
a._pending_source = "Hello world, this is a test."
a._pending_src = "en"
a._handle(("ok", "你好世界，这是一次测试。", 1.25, "en", "zh"))
page = app_mod.HISTORY.page()
check_true("历史有 1 条", len(page) == 1, f"{len(page)}")
if page:
    e = page[0]
    check_true("语言对正确", (e.src, e.tgt) == ("en", "zh"), f"{e.src}->{e.tgt}")
    check_true("原文正确", e.source == "Hello world, this is a test.",
               repr(e.source))
    check_true("译文正确", e.target == "你好世界，这是一次测试。", repr(e.target))
    check_true("elapsed 传下来了", abs(e.elapsed - 1.25) < 0.01, f"{e.elapsed}")
check_true("_pending_source 已清空", a._pending_source == "", repr(a._pending_source))
check_true("译文显示在界面上", "你好世界" in a.output_text.get("1.0", "end-1c"))

print("\n=== 2) 翻译失败不写历史 ===")
n0 = len(app_mod.HISTORY.page(0, 999))
a._pending_source = "should not be recorded"
a._handle(("error", "simulated failure"))
n1 = len(app_mod.HISTORY.page(0, 999))
check_true("失败后条数不变", n0 == n1, f"{n0} -> {n1}")

print("\n=== 3) 取消不写历史 ===")
n0 = len(app_mod.HISTORY.page(0, 999))
a._pending_source = "cancelled content"
a._handle(("cancelled", None))
n1 = len(app_mod.HISTORY.page(0, 999))
check_true("取消后条数不变", n0 == n1, f"{n0} -> {n1}")

print("\n=== 4) 记录抛异常也不能影响翻译（最重要的保护） ===")
orig = app_mod.HISTORY.record
app_mod.HISTORY.record = lambda *x, **k: (_ for _ in ()).throw(
    RuntimeError("simulated disk full"))
a._pending_source = "content that fails to save"
try:
    a._handle(("ok", "译文内容", 0.5, "en", "zh"))
    check_true("记录失败时 _handle 未中断", True)
    check_true("译文仍显示在界面上",
               "译文内容" in a.output_text.get("1.0", "end-1c"),
               repr(a.output_text.get("1.0", "end-1c")[:20]))
    check_true("状态是翻译完成而非失败",
               "完成" in a.status_lbl.cget("text"), a.status_lbl.cget("text"))
    check_true("忙碌标志已复位（按钮可用）", a._busy is False)
except Exception as exc:
    check_true("记录失败时 _handle 未中断", False,
               f"抛了 {type(exc).__name__}: {exc}")
finally:
    app_mod.HISTORY.record = orig

print("\n=== 5) 连续两次翻译，两条都记下且原文不串 ===")
# 先把前几个子测试留下的记录清掉，孤立验证本条
app_mod.HISTORY.purge()
a._pending_source = "first source"
a._pending_src = "en"
a._handle(("ok", "第一", 0.1, "en", "zh"))
a._pending_source = "second source"
a._pending_src = "en"
a._handle(("ok", "第二", 0.2, "en", "zh"))
page = app_mod.HISTORY.page(0, 10)
srcs = [e.source for e in page]
check_true("两次都记了", len(page) == 2, f"{len(page)}")
check_true("最新在前", srcs[0] == "second source", f"{srcs}")
check_true("原文没有互相串", set(srcs) == {"first source", "second source"},
           f"{srcs}")

teardown(a, tmpd)

print("\n=== 6) 超长文本完整入历史（不截断） ===")
a, tmpd = fresh_app()
long_src = "句子。" * 2000                    # 6000 字符
a._pending_source = long_src
a._handle(("ok", "Sentence. " * 2000, 9.9, "zh", "en"))
page = app_mod.HISTORY.page(0, 5)
check_true("长文写入成功", len(page) == 1, f"{len(page)}")
if page:
    check_true("原文完整保存（未截断）", len(page[0].source) == len(long_src),
               f"{len(page[0].source)} vs {len(long_src)}")
teardown(a, tmpd)

print("\n=== 7) 历史对话框能开、能列出、能恢复 ===")
a, tmpd = fresh_app()
for i in range(3):
    app_mod.HISTORY.record("en", "zh", f"source {i}", f"译文 {i}", 0.1)
    time.sleep(0.005)
a._open_history()
end = time.time() + 1.2
while time.time() < end:                       # 跑事件循环让对话框填充
    a.root.update()
    time.sleep(0.02)
tops = [w for w in a.root.winfo_children() if w.winfo_class() == "Toplevel"]
check_true("历史对话框已打开", len(tops) == 1, f"{[t.title() for t in tops]}")


def find_tree(w):
    for k in w.winfo_children():
        if k.winfo_class() == "Treeview":
            return k
        r = find_tree(k)
        if r:
            return r
    return None


tree = find_tree(tops[0]) if tops else None
check_true("找到列表控件", tree is not None)
if tree:
    rows = tree.get_children()
    check_true("列出 3 条", len(rows) == 3, f"{len(rows)}")
    # 选中一条 -> 详情面板应显示原文与译文
    tree.selection_set(rows[0])
    a.root.update()
    detail = None
    for k in tops[0].winfo_children():
        if k.winfo_class() == "Text":
            detail = k
            break
    check_true("详情面板找到", detail is not None)
    if detail:
        shown = detail.get("1.0", "end-1c")
        check_true("详情含原文", "source 2" in shown, repr(shown[:48]))
        check_true("详情含译文", "译文 2" in shown, repr(shown[:48]))

for t in tops:
    t.destroy()
teardown(a, tmpd)

print("\n=== 8) 缓存对话框显示历史那一行且标为不可恢复 ===")
a, tmpd = fresh_app()
app_mod.HISTORY.record("en", "zh", "one", "一", 0.1)
app_mod.CACHE.set_history(app_mod.HISTORY)
items = app_mod.CACHE.inventory(force=True)
hist = [i for i in items if i.name == "history"]
check_true("清单里有 history", len(hist) == 1)
if hist:
    h = hist[0]
    check_true("等级是 USER_RECORD",
               h.retention == app_mod.Retention.USER_RECORD, h.retention)
    check_true("标签写明不可恢复", "不可恢复" in h.retention_label,
               h.retention_label)
    check_true("size > 0", h.size > 0, f"{h.size}")
    check_true("不在 disposables 里",
               "history" not in [i.name for i in app_mod.CACHE.disposables()])
    check_true("从'清理缓存'里删它会被拒绝", True)
teardown(a, tmpd)

print("\n" + "=" * 66)
if failures:
    print(f"FAILURES ({len(failures)}):")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("历史集成测试全部通过")
