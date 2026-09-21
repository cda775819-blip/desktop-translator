# -*- coding: utf-8 -*-
"""Test CacheManager through its interface only.

Isolation strategy: CacheManager takes its locations as constructor params, so
tests point it at a tmp directory. The filesystem underneath is real -- no
mocking of os/shutil. That's a "local-substitutable" dependency: the stand-in is
an actual temp dir, so we never test past the interface.

The one thing NOT tested here is the behaviour of the real app: that's what
test_cache_app.py does against the actual deployment.
"""
import io
import os
import shutil
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\translator-build")
import _paths

app = _paths.load_app("ta")

failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not cond:
        failures.append(label)


def make_tree(root, spec):
    """spec: {relpath: bytes}"""
    for rel, size in spec.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(b"x" * size)


class Fixture:
    """搭一个假的数据目录，形状和真实部署一致。"""

    def __init__(self, with_history=True):
        self.root = tempfile.mkdtemp(prefix="cachetest-")
        self.data_root = os.path.join(self.root, "Translator")
        self.cache = os.path.join(self.data_root, "cache")
        self.packages = os.path.join(self.data_root, "models", "packages")
        self.logs = os.path.join(self.data_root, "logs")
        self.argos_data = os.path.join(self.data_root, "data", "argos-translate")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.data_root, "data")
        make_tree(self.data_root, {
            "models/packages/en_zh/model.bin": 5000,
            "models/packages/zh_en/model.bin": 3000,
            "cache/minisbd/en.onnx": 200,
            "cache/minisbd/zh-hans.onnx": 600,
            "cache/argos-translate/downloads/partial.tmp": 900,
            "data/argos-translate/index.json": 1300,
            "logs/app.log": 400,
        })
        # data_dir 是显式参数（不读环境变量），所以传进去而不是靠 setdefault
        self.mgr = app.CacheManager(self.data_root, self.cache,
                                    self.packages, self.logs, self.argos_data)
        self.history = None
        if with_history:
            # 历史存储也指到临时目录；用实例而不是 HISTORY 单例
            self.history = app.HistoryStore(
                os.path.join(self.data_root, "history.jsonl"))
            self.mgr.set_history(self.history)

    def path(self, rel):
        return os.path.join(self.data_root, rel)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def total_of(items):
    return sum(i.size for i in items)


print("=== 1) inventory 报告所有项，含不可删的模型与不可恢复的历史 ===")
fx = Fixture()
fx.history.record("en", "zh", "hello", "你好", 0.1)
inv = fx.mgr.inventory(force=True)
names = [i.name for i in inv]
print(f"  项: {names}")
for i in inv:
    print(f"    {i.name:<12} {i.size:>7} B  {i.retention:<12} {i.retention_label}")
check("包含 models", "models" in names)
check("包含 minisbd", "minisbd" in names)
check("包含 argos-cache", "argos-cache" in names)
check("包含 logs", "logs" in names)
check("包含 history", "history" in names)
by = {i.name: i for i in inv}
check("models 大小 = 8000", by["models"].size == 8000, f"{by['models'].size}")
check("minisbd 大小 = 800", by["minisbd"].size == 800, f"{by['minisbd'].size}")
check("argos-data 大小 = 1300", by["argos-data"].size == 1300, f"{by['argos-data'].size}")
check("logs 大小 = 400", by["logs"].size == 400, f"{by['logs'].size}")
check("history 大小 > 0（历史是文件，不能被报成 0）", by["history"].size > 0,
      f"{by['history'].size}")
check("每个项都有 retention", all(i.retention for i in inv))

print("\n=== 1b) 三种保留等级各自正确 ===")
check("models 是 ASSET", by["models"].retention == app.Retention.ASSET,
      by["models"].retention)
check("history 是 USER_RECORD",
      by["history"].retention == app.Retention.USER_RECORD,
      by["history"].retention)
check("minisbd 是 DISPOSABLE",
      by["minisbd"].retention == app.Retention.DISPOSABLE,
      by["minisbd"].retention)
check("ASSET 不可删", by["models"].deletable is False)
check("ASSET 不算 disposable", by["models"].disposable is False)
check("USER_RECORD 可删", by["history"].deletable is True)
check("USER_RECORD 不算 disposable（不能混进'清理缓存'）",
      by["history"].disposable is False)
check("DISPOSABLE 可删且 disposable", by["minisbd"].deletable is True
      and by["minisbd"].disposable is True)

print("\n=== 2) disposables() 既不含 models 也不含 history ===")
dis = fx.mgr.disposables()
dnames = sorted(i.name for i in dis)
print(f"  可清理项: {dnames}")
check("models 不在可清理列表", "models" not in dnames)
check("history 不在可清理列表（关键：不能混进缓存清理）",
      "history" not in dnames)
check("disposable_size 不含模型与历史",
      fx.mgr.disposable_size() == 800 + 900 + 1300 + 400,
      f"{fx.mgr.disposable_size()}")
check("total_size 含模型、历史",
      fx.mgr.total_size() == 8000 + 800 + 900 + 1300 + 400 + by["history"].size,
      f"{fx.mgr.total_size()}")

print("\n=== 3) clear('models') 与 clear('history') 都必须被拒绝 ===")
for bad in ("models", "history"):
    try:
        fx.mgr.clear([bad])
        check(f"clear({bad!r}) 抛 KeyError", False, "竟然没抛")
    except KeyError as e:
        check(f"clear({bad!r}) 抛 KeyError", True, str(e)[:60])
check("模型文件仍在", os.path.exists(fx.path("models/packages/en_zh/model.bin")))
check("历史仍在", fx.history.summary().count == 1)

print("\n=== 4) clear() 清掉可再生的项，且不碰历史 ===")
rep = fx.mgr.clear()
print(f"  summary: {rep.summary()}")
print(f"  cleared={rep.cleared} deferred={len(rep.deferred)} blocked={len(rep.blocked)}")
check("freed = 3400 (minisbd+argos-cache+argos-data+logs)",
      rep.freed == 800 + 900 + 1300 + 400, f"{rep.freed}")
check("minisbd 目录已删", not os.path.exists(fx.path("cache/minisbd")))
check("argos-cache 已删", not os.path.exists(fx.path("cache/argos-translate")))
check("logs 已删", not os.path.exists(fx.path("logs")))
check("模型未被触碰", os.path.exists(fx.path("models/packages/en_zh/model.bin")))
check("历史未被触碰（关键）", fx.history.summary().count == 1,
      f"{fx.history.summary().count}")
check("cleared 里没有 history", "history" not in rep.cleared)
check("模型仍在清单里", "models" in [i.name for i in fx.mgr.inventory()])
by2 = {i.name: i for i in fx.mgr.inventory()}
check("清单已刷新（minisbd 归零）", by2["minisbd"].size == 0, f"{by2['minisbd'].size}")
check("模型大小未变", by2["models"].size == 8000)

print("\n=== 5) clear(单项) ===")
make_tree(fx.data_root, {"cache/minisbd/en.onnx": 111})
rep2 = fx.mgr.clear(["minisbd"])
print(f"  cleared={rep2.cleared} freed={rep2.freed}")
check("只清了 minisbd", rep2.cleared == ["minisbd"], f"{rep2.cleared}")
check("freed = 111", rep2.freed == 111, f"{rep2.freed}")

print("\n=== 6) 未知名字必须报错（不静默忽略） ===")
try:
    fx.mgr.clear(["nope"])
    check("clear('nope') 抛 KeyError", False, "竟然没抛")
except KeyError:
    check("clear('nope') 抛 KeyError", True)

print("\n=== 6b) purge() 清历史，且不碰缓存与模型 ===")
make_tree(fx.data_root, {"cache/minisbd/keep.onnx": 555})
for i in range(3):
    fx.history.record("en", "zh", f"t{i}", f"译{i}", 0.1)
before = fx.history.summary().count
check("purge 前有历史", before >= 3, f"{before}")
rep3 = fx.mgr.purge()
print(f"  purge: cleared={rep3.cleared} freed={rep3.freed}")
check("cleared 含 history", "history" in rep3.cleared, f"{rep3.cleared}")
check("历史已清空", fx.history.summary().count == 0,
      f"{fx.history.summary().count}")
check("purge 不动缓存", os.path.exists(fx.path("cache/minisbd/keep.onnx")))
check("purge 不动模型",
      os.path.exists(fx.path("models/packages/en_zh/model.bin")))
check("purge 释放量 > 0", rep3.freed > 0, f"{rep3.freed}")

print("\n=== 6c) purge('models') 被拒绝 ===")
try:
    fx.mgr.purge(["models"])
    check("purge('models') 抛 KeyError", False, "竟然没抛")
except KeyError as e:
    check("purge('models') 抛 KeyError", True, str(e)[:60])
check("模型仍在", os.path.exists(fx.path("models/packages/en_zh/model.bin")))

print("\n=== 6d) 委托删除后 store 的内存状态同步（不能出现'文件没了内存还在'） ===")
fx5 = Fixture()
for i in range(4):
    fx5.history.record("en", "zh", f"m{i}", f"n{i}", 0.1)
check("委托前 store 读到 4 条", fx5.history.page(0, 10).__len__() == 4,
      f"{len(fx5.history.page(0, 10))}")
fx5.mgr.purge(["history"])
check("委托后 store 立刻读到 0 条（缓存已失效）",
      len(fx5.history.page(0, 10)) == 0, f"{len(fx5.history.page(0, 10))}")
check("清理报告也归零", fx5.mgr.inventory(force=True)
      and [i for i in fx5.mgr.inventory() if i.name == "history"][0].size == 0)
fx5.cleanup()

print("\n=== 7) 删不掉的项：改名释放空间 + 记入 deferred ===")

fx2 = Fixture()
mgr2 = fx2.mgr
# 让 logs 目录里"另一个"文件删不掉，而 app.log 正常删除。
# 这样 logs 目录本身会被留下（因为还有一个删不掉的文件），改名才有地方放
# —— 这是真实场景：目录里混着占用和未占用的文件。
make_tree(fx2.data_root, {"logs/held.txt": 777})
real_remove = app._remove_tree
blocked_file = fx2.path("logs/held.txt")


def fake_remove(path):
    if os.path.normcase(path) == os.path.normcase(fx2.logs):
        # 手工删除可删的，把 held.txt 报为失败
        freed = 0
        failures = []
        for n in os.listdir(path):
            f = os.path.join(path, n)
            if os.path.normcase(f) == os.path.normcase(blocked_file):
                failures.append(f)
                continue
            try:
                freed += os.path.getsize(f)
                os.remove(f)
            except OSError:
                failures.append(f)
        return freed, failures
    return real_remove(path)


app._remove_tree = fake_remove
try:
    rep3 = mgr2.clear(["logs"])
finally:
    app._remove_tree = real_remove

print(f"  deferred={[os.path.basename(p) for p in rep3.deferred]}")
print(f"  blocked={rep3.blocked}")
check("被占用的文件进了 deferred", len(rep3.deferred) == 1, f"{len(rep3.deferred)}")
check("deferred 文件已改名", all(".trash-" in p for p in rep3.deferred),
      f"{rep3.deferred}")
check("改名后空间已计入释放量 (400+777)", rep3.freed == 1177, f"{rep3.freed}")
check("blocked 为空", not rep3.blocked, f"{rep3.blocked}")
check("原文件名已不存在", not os.path.exists(blocked_file))

print("\n=== 8) sweep_deferred 清掉上次的残留 ===")
found = []
for root, _d, names in os.walk(fx2.data_root):
    found += [os.path.join(root, n) for n in names if ".trash-" in n]
print(f"  残留的 .trash-* 文件: {len(found)} -> {[os.path.basename(p) for p in found]}")
n = mgr2.sweep_deferred()
print(f"  sweep_deferred() 清掉 {n} 个")
check("sweep 清掉了残留", n >= 1, f"{n}")
still = [p for p in found if os.path.exists(p)]
check("残留已不存在", not still, f"{still}")

print("\n=== 9) 不存在的目录不报错 ===")
fx3 = Fixture()
shutil.rmtree(fx3.path("cache"), ignore_errors=True)
inv3 = fx3.mgr.inventory()
by3 = {i.name: i.size for i in inv3}
check("缺失目录按 0 计", by3["minisbd"] == 0, f"{by3['minisbd']}")
rep4 = fx3.mgr.clear(["minisbd"])
check("清理缺失目录不报错", rep4.freed == 0, f"{rep4.freed}")

print("\n=== 10) 重复清理是幂等的 ===")
fx4 = Fixture()
r1 = fx4.mgr.clear()
r2 = fx4.mgr.clear()
check("第二次清理释放 0", r2.freed == 0, f"{r2.freed}")
check("第一次释放 3400", r1.freed == 3400, f"{r1.freed}")
check("没有 blocked", not r2.blocked)

for f in (fx, fx2, fx3, fx4):
    f.cleanup()

print("\n" + "=" * 62)
if failures:
    print(f"FAILURES ({len(failures)}):")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("缓存模块测试全部通过")
