# -*- coding: utf-8 -*-
"""Test HistoryStore through its interface only.

The dependency is "local-substitutable": the real filesystem, pointed at a temp
directory. No mocking of os/json -- the stand-in is a real directory, so tests
never reach past the interface.

What is deliberately NOT tested here: the app's UI. That's test_history_app.py.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\translator-build")
import _paths

app = _paths.load_app("ta")

failures = []


def check_true(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label:<52} {detail}")
    if not cond:
        failures.append(label)


def tmp_store(max_entries=200, max_bytes=8 * 1024 * 1024):
    d = tempfile.mkdtemp(prefix="histtest-")
    path = os.path.join(d, "history.jsonl")
    return app.HistoryStore(path, max_entries=max_entries,
                            max_bytes=max_bytes), d


print("=== 1) 构造时不该碰磁盘（否则缓存清理的 sweep 会留残留） ===")
st, d = tmp_store()
check_true("构造后目录里没有新文件",
           not os.path.exists(st.path),
           f"exists={os.path.exists(st.path)}")
shutil.rmtree(d, ignore_errors=True)

print("\n=== 2) record 后能读回来 ===")
st, d = tmp_store()
out = st.record("en", "zh", "Hello world.", "你好，世界。", 0.42)
check_true("record 返回成功", bool(out), repr(out))
check_true("文件已创建", os.path.exists(st.path))
page = st.page()
check_true("page 返回 1 条", len(page) == 1, f"{len(page)}")
if page:
    e = page[0]
    check_true("src/tgt 正确", (e.src, e.tgt) == ("en", "zh"), f"{e.src}->{e.tgt}")
    check_true("原文正确", e.source == "Hello world.", repr(e.source))
    check_true("译文正确", e.target == "你好，世界。", repr(e.target))
    check_true("elapsed 保留", abs(e.elapsed - 0.42) < 0.01, f"{e.elapsed}")
shutil.rmtree(d, ignore_errors=True)

print("\n=== 3) 最新在前（倒序） ===")
st, d = tmp_store()
for i in range(5):
    st.record("en", "zh", f"line {i}", f"行 {i}", 0.1)
    time.sleep(0.005)                      # 拉开 ts，避免同秒排序不稳
page = st.page()
texts = [e.source for e in page]
check_true("5 条都在", len(page) == 5, f"{len(page)}")
check_true("最新的在最前", texts[0] == "line 4", f"{texts}")

print("\n=== 4) 分页 ===")
p1 = st.page(0, 2)
p2 = st.page(2, 2)
p3 = st.page(4, 2)
p4 = st.page(99, 2)
check_true("第 1 页 2 条", len(p1) == 2, f"{[e.source for e in p1]}")
check_true("第 2 页 2 条", len(p2) == 2, f"{[e.source for e in p2]}")
check_true("第 3 页 1 条", len(p3) == 1, f"{[e.source for e in p3]}")
check_true("越界返回空", p4 == [], f"{p4}")
check_true("分页不重叠", {e.source for e in p1} & {e.source for e in p2} == set())
shutil.rmtree(d, ignore_errors=True)

print("\n=== 5) record 永不抛异常（调用点在翻译成功之后） ===")
st, d = tmp_store()
cases = [
    ("空原文", ("en", "zh", "", "x", 0.0)),
    ("纯空白原文", ("en", "zh", "   \n ", "x", 0.0)),
    ("None 原文", ("en", "zh", None, "x", 0.0)),
]
for label, args in cases:
    try:
        r = st.record(*args)
        check_true(f"{label} 不抛且返回失败", r.ok is False, repr(r))
    except Exception as exc:
        check_true(f"{label} 不抛", False, f"抛了 {type(exc).__name__}: {exc}")

# 目录不可写时也不能抛
st2, d2 = tmp_store()
os.makedirs(os.path.dirname(st2.path), exist_ok=True)
# 用一个"路径是目录"的非法目标来触发写入失败
bad_path = os.path.join(d2, "history.jsonl")
os.makedirs(bad_path)                       # 同名目录 -> open() 会失败
st3 = app.HistoryStore(bad_path)
try:
    r = st3.record("en", "zh", "hello", "你好", 0.1)
    check_true("写入失败时不抛", r.ok is False, repr(r))
except Exception as exc:
    check_true("写入失败时不抛", False, f"抛了 {type(exc).__name__}: {exc}")
shutil.rmtree(d, ignore_errors=True)
shutil.rmtree(d2, ignore_errors=True)

print("\n=== 6) 条数上限：从最旧的一端淘汰 ===")
st, d = tmp_store(max_entries=5, max_bytes=10 ** 9)
for i in range(12):
    st.record("en", "zh", f"item-{i}", f"项-{i}", 0.1)
    time.sleep(0.003)
page = st.page(0, 100)
srcs = [e.source for e in page]
check_true("只保留 5 条", len(page) == 5, f"{len(page)}")
check_true("保留的是最新 5 条", srcs == ["item-11", "item-10", "item-9",
                                        "item-8", "item-7"], f"{srcs}")
check_true("文件行数也是 5",
           len([l for l in open(st.path, encoding="utf-8") if l.strip()]) == 5)
check_true("summary 一致", st.summary().count == 5, f"{st.summary()}")
shutil.rmtree(d, ignore_errors=True)

print("\n=== 7) 字节上限：长文不会把历史撑爆 ===")
st, d = tmp_store(max_entries=1000, max_bytes=4096)
big = "x" * 1000
for i in range(20):
    st.record("en", "zh", f"{big}-{i}", f"{big}-{i}", 0.1)
    time.sleep(0.002)
size = os.path.getsize(st.path)
page = st.page(0, 1000)
check_true("文件不超字节上限", size <= 4096, f"{size} bytes")
check_true("至少留了最新一条", len(page) >= 1, f"{len(page)} 条")
check_true("最新的还在", page[0].source.endswith("-19"), page[0].source[-6:])
check_true("确实淘汰了旧的", len(page) < 20, f"剩 {len(page)} 条")
shutil.rmtree(d, ignore_errors=True)

print("\n=== 8) 单条内容完整保存（不截断） ===")
st, d = tmp_store(max_entries=10, max_bytes=10 ** 7)
long_text = "句子。" * 3000                  # 9000 字符
st.record("zh", "en", long_text, "Sentence. " * 3000, 1.0)
page = st.page()
check_true("9000 字原文完整保存",
           len(page[0].source) == len(long_text),
           f"{len(page[0].source)} vs {len(long_text)}")
check_true("未标记截断", page[0].truncated is False)
shutil.rmtree(d, ignore_errors=True)

print("\n=== 9) 损坏行不能让整份历史挂掉 ===")
st, d = tmp_store()
st.record("en", "zh", "good one", "好的", 0.1)
with open(st.path, "a", encoding="utf-8") as fh:
    fh.write("{ 这不是合法 json\n")
    fh.write("\n")                            # 空行
    fh.write('{"src":"en"}\n')                # 缺字段
st.record("en", "zh", "good two", "好的二", 0.1)
st2 = app.HistoryStore(st.path)               # 全新实例，强制从磁盘重读
page = st2.page(0, 100)
srcs = [e.source for e in page]
check_true("好的记录仍在", "good one" in srcs and "good two" in srcs, f"{srcs}")
check_true("坏行被跳过", len(page) == 2, f"{len(page)}")
shutil.rmtree(d, ignore_errors=True)

print("\n=== 10) 崩溃在写一半：只损失最后一条 ===")
st, d = tmp_store()
for i in range(3):
    st.record("en", "zh", f"ok-{i}", f"好-{i}", 0.1)
with open(st.path, "a", encoding="utf-8") as fh:
    fh.write('{"src":"en","tgt":"zh","source":"torn')   # 模拟被强杀
st2 = app.HistoryStore(st.path)
page = st2.page(0, 100)
check_true("前 3 条完好", len(page) == 3, f"{len(page)}")
check_true("撕裂的最后一行被丢弃", all(not e.source.startswith("{") for e in page))
shutil.rmtree(d, ignore_errors=True)

print("\n=== 11) purge 清空 ===")
st, d = tmp_store()
for i in range(4):
    st.record("en", "zh", f"x-{i}", f"y-{i}", 0.1)
check_true("purge 前有 4 条", st.summary().count == 4, f"{st.summary().count}")
r = st.purge()
check_true("purge 成功", bool(r), repr(r))
check_true("purge 后为 0", st.summary().count == 0, f"{st.summary().count}")
check_true("文件已删除或为空",
           not os.path.exists(st.path) or os.path.getsize(st.path) == 0)
check_true("purge 后还能继续记录", bool(st.record("en", "zh", "again", "再来", 0.1)))
check_true("新记录可读", st.summary().count == 1, f"{st.summary().count}")
shutil.rmtree(d, ignore_errors=True)

print("\n=== 12) purge 幂等 ===")
st, d = tmp_store()
check_true("空历史 purge 不报错", bool(st.purge()))
check_true("再 purge 一次也不报错", bool(st.purge()))
shutil.rmtree(d, ignore_errors=True)

print("\n=== 13) summary 字段 ===")
st, d = tmp_store(max_entries=77, max_bytes=123456)
st.record("en", "zh", "a", "b", 0.1)
s = st.summary()
check_true("count 正确", s.count == 1, f"{s.count}")
check_true("bytes > 0", s.bytes > 0, f"{s.bytes}")
check_true("上报条数上限", s.max_entries == 77, f"{s.max_entries}")
check_true("上报字节上限", s.max_bytes == 123456, f"{s.max_bytes}")
check_true("上报路径", s.path == st.path, s.path)
shutil.rmtree(d, ignore_errors=True)

print("\n=== 14) 跨实例持久化 ===")
st, d = tmp_store()
st.record("ja", "zh", "これはテストです。", "这是测试。", 0.3)
st2 = app.HistoryStore(st.path)
page = st2.page()
check_true("新实例能读到", len(page) == 1, f"{len(page)}")
if page:
    check_true("内容一致", page[0].source == "これはテストです。", page[0].source)
    check_true("语言对一致", (page[0].src, page[0].tgt) == ("ja", "zh"))
shutil.rmtree(d, ignore_errors=True)

print("\n=== 15) 特殊字符与 emoji 不破坏 JSONL ===")
st, d = tmp_store()
tricky = 'He said "hi"\n newline\ttab \\ backslash 😀 emoji'
st.record("en", "zh", tricky, tricky, 0.1)
st2 = app.HistoryStore(st.path)
page = st2.page()
check_true("往返一致", len(page) == 1 and page[0].source == tricky,
           repr(page[0].source) if page else "空")
nlines = len([l for l in open(st.path, encoding="utf-8") if l.strip()])
check_true("仍然只占一行", nlines == 1, f"{nlines} 行")
shutil.rmtree(d, ignore_errors=True)

print("\n=== 16) 非 ASCII 路径（sentencepiece 那类陷阱） ===")
d = tempfile.mkdtemp(prefix="histtest-")
sub = os.path.join(d, "中文目录")
os.makedirs(sub)
st = app.HistoryStore(os.path.join(sub, "history.jsonl"))
st.record("en", "zh", "hello", "你好", 0.1)
check_true("中文路径下可写可读", st.summary().count == 1, f"{st.summary()}")
shutil.rmtree(d, ignore_errors=True)

print("\n" + "=" * 64)
if failures:
    print(f"FAILURES ({len(failures)}):")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("历史模块测试全部通过")
