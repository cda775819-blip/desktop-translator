# ======================================================================
# 7. 缓存管理
# ======================================================================
#
# 这个程序在磁盘上留下好几摊东西，来源不同、能不能删也不同。所以每项都带一个
# **保留等级**（retention），删除入口按等级决定放不放行：
#
#   ASSET        用户资产   翻译模型。删了要重新下几十到几百 MB。
#                           **绝不提供删除** —— 接口层就没有这个可能。
#   USER_RECORD  用户记录   翻译历史。可以删，但删了**不可恢复**，
#                           所以界面必须单独确认，不能和缓存混在一个按钮里。
#   DISPOSABLE   可再生     分句模型 / 清单索引 / 日志。删了下次自动重建。
#
# 为什么不是"历史也塞进 DISPOSABLE"：那样界面一个"清理缓存"按钮就把用户的
# 翻译记录一起清掉了，而它既不可恢复、也不是缓存。
#
# 报告与删除分开：
#   inventory()   报告**所有**占空间的东西（含不可删的），让用户看得见。
#   clear()       只删 DISPOSABLE。
#   purge()       只删 USER_RECORD（不可恢复，调用方负责确认）。
#
# 历史的删除**委托**给 HistoryStore：历史的内存状态在它那里，绕过它直接删文件
# 会让它的缓存失效（文件删了、内存还记着）。

import os                        # noqa: E402
import shutil                    # noqa: E402
import time as _time             # noqa: E402


class Retention:
    """一项磁盘数据的保留等级。字符串值方便直接显示和比较。"""

    ASSET = "asset"                # 用户资产，不提供删除
    USER_RECORD = "user_record"    # 用户记录，可删但不可恢复
    DISPOSABLE = "disposable"      # 可再生，删了会重建

    LABELS = {
        ASSET: "用户资产 · 不清理",
        USER_RECORD: "用户记录 · 删除不可恢复",
        DISPOSABLE: "可再生 · 可清理",
    }


class CacheItem:
    """一项磁盘数据的事实：在哪、多大、是什么、属于哪个保留等级。"""

    __slots__ = ("name", "label", "path", "purpose", "retention", "size")

    def __init__(self, name, label, path, purpose, retention, size=0):
        self.name = name
        self.label = label
        self.path = path
        self.purpose = purpose
        self.retention = retention
        self.size = size

    @property
    def disposable(self) -> bool:
        """兼容旧调用：界面用它判断能不能出现在"清理缓存"里。"""
        return self.retention == Retention.DISPOSABLE

    @property
    def deletable(self) -> bool:
        """凡不是用户资产的，都提供了删除入口（但确认方式不同）。"""
        return self.retention != Retention.ASSET

    @property
    def retention_label(self) -> str:
        return Retention.LABELS.get(self.retention, self.retention)

    def __repr__(self):
        return (f"CacheItem({self.name!r}, {self.size} B, "
                f"{self.retention})")


class CacheManager:
    """磁盘数据的事实来源（source of truth）。

    对外四个动作：
        inventory()          报告所有项目及大小（含不可删的用户资产）
        disposables()        只报告可再生的
        clear(names=None)    删除指定的可再生项，返回 ClearReport
        purge(names=None)    删除指定的用户记录（历史），返回 ClearReport

    约束（调用方必须知道的）：
      * inventory() 会遍历整个 models 目录，实测约 350 ms / 20 GB 量级。
        **不要在 UI 线程同步调用**，用它包一层线程或先看 cached_inventory()。
      * clear() / purge() 允许删除"正在被引用的文件"：删不掉时会改名成
        .trash-* 并记入 deferred，此时空间已经释放（NTFS 允许改名被占用的
        文件），真正清空发生在下次启动的 sweep_deferred()。
      * clear() 只接受 DISPOSABLE，purge() 只接受 USER_RECORD，两者都不接受
        用户资产。传错会抛 KeyError —— 故意的，删除入口不该存在误删资产的可能。
      * 用户记录的删除**委托**给 history_store：历史的内存状态在它那里，
        绕过它删文件会让它缓存失效。
    """

    def __init__(self, data_root, cache_dir, packages_dir, log_dir, data_dir):
        """所有位置都由调用方传入。

        刻意不在这里读 XDG_DATA_HOME：环境变量是隐藏的输入，会让这个模块
        依赖 import 顺序、也没法在测试里指到别处。路径属于 interface。

        历史存储通过 set_history() 注入，而不是构造参数 —— 因为 CACHE 单例
        在本段就建好了，而 HistoryStore 定义在下一段。
        """
        self._history = None
        self._entries = [
            ("models", "翻译模型", packages_dir, Retention.ASSET,
             "各语言对的翻译模型。删除后需要重新下载（每对 80–160 MB）。"),
            ("minisbd", "分句模型", os.path.join(cache_dir, "minisbd"),
             Retention.DISPOSABLE,
             "把长文切成句子的 onnx 模型。随程序预置一份，缺失时会重新下载。"),
            ("argos-cache", "模型清单与暂存",
             os.path.join(cache_dir, "argos-translate"), Retention.DISPOSABLE,
             "可下载模型的下标索引、下载中转文件。删除后下次刷新清单时重建。"),
            ("argos-data", "运行时数据", data_dir, Retention.DISPOSABLE,
             "argostranslate 自己写的数据目录（索引副本等）。"),
            ("logs", "运行日志", log_dir, Retention.DISPOSABLE,
             "app.log 与 selftest.txt。删了只影响事后排查问题。"),
        ]
        self._roots = [packages_dir, cache_dir, log_dir, data_dir]
        self._inventory: list[CacheItem] | None = None
        self._stamp = 0.0

    # -- 报告 ----------------------------------------------------------

    def set_history(self, store) -> None:
        """注入历史存储。

        必须在第一次 inventory() 之前调用，否则历史不会出现在清单里。
        注入而不是构造参数：CACHE 单例在本段就建好了，HistoryStore 在下一段。
        """
        self._history = store
        self._inventory = None          # 让下次 inventory() 带上历史

    def inventory(self, force: bool = False) -> list[CacheItem]:
        """列出所有项目（含不可删的模型），附带大小。

        结果会被缓存；目录变动后用 force=True 或 refresh() 重算。
        """
        if self._inventory is not None and not force:
            return list(self._inventory)
        items = []
        for name, label, path, retention, purpose in self._entries:
            items.append(CacheItem(name, label, path, purpose, retention,
                                   measure_path(path)))
        if self._history is not None:
            try:
                st = self._history.summary()
                items.append(CacheItem(
                    "history", "翻译历史", st.path,      # path
                    "你翻译过的原文与译文。删了不可恢复；"  # purpose（第 4 个）
                    f"上限 {st.max_entries} 条 / "
                    f"{_human(st.max_bytes)}，超了自动淘汰最旧的。",
                    Retention.USER_RECORD,               # retention（第 5 个）
                    st.bytes))
            except Exception:
                log.exception("history summary failed")
        self._inventory = items
        self._stamp = _time.time()
        return list(items)

    def disposables(self, force: bool = False) -> list[CacheItem]:
        """只列出可再生的缓存项 —— 删除入口只认这些名字。"""
        return [i for i in self.inventory(force) if i.disposable]

    def cached_inventory(self) -> list[CacheItem] | None:
        """如果已经算过就直接给，否则 None。给 UI 做"先显示旧的再刷新"。"""
        return list(self._inventory) if self._inventory is not None else None

    def total_size(self, force: bool = False) -> int:
        return sum(i.size for i in self.inventory(force))

    def disposable_size(self, force: bool = False) -> int:
        return sum(i.size for i in self.disposables(force))

    def refresh(self) -> list[CacheItem]:
        return self.inventory(force=True)

    def sweep_deferred(self) -> int:
        """清掉上次删不掉的 .trash-* 残留。启动时调一次。

        用递归遍历而不是"按已知路径猜父目录"：残留可能出现在任何一层，
        而且它所在的原目录本身可能已经被删掉了（父目录没了就永远猜不到）。

        返回删掉的条目数。仍被占用的会留着，下次启动再试。
        """
        removed = 0
        seen: set[str] = set()
        for root_path in self._roots:
            # 覆盖 root_path 自身 + 它的父目录（残留是"原地改名"，可能就在旁边）
            for base in (root_path, os.path.dirname(root_path)):
                if not base or base in seen or not os.path.isdir(base):
                    continue
                seen.add(base)
                for root, dirs, names in os.walk(base, onerror=lambda e: None):
                    victims = [n for n in dirs + names if ".trash-" in n]
                    for n in victims:
                        victim = os.path.join(root, n)
                        try:
                            if os.path.isdir(victim):
                                shutil.rmtree(victim)
                            else:
                                os.remove(victim)
                            removed += 1
                            log.info("swept deferred deletion: %s", victim)
                        except OSError:
                            pass                  # 还被占用，下次启动再说
        if removed:
            self._inventory = None
        return removed

    # -- 删除 ----------------------------------------------------------

    def _resolve(self, names, wanted: str):
        """挑出要删的项。按保留等级过滤，等级不符就报错。

        等级不符时报错而不是静默跳过：调用方传错名字应当立刻发现，
        而不是以为删掉了。
        """
        pool = {i.name: i for i in self.inventory(force=True)
                if i.retention == wanted}
        if names is None:
            return list(pool.values())
        targets = []
        for n in names:
            if n not in pool:
                why = ("用户资产（翻译模型）不提供删除"
                       if n == "models" else
                       f"它不是{'可再生缓存' if wanted == Retention.DISPOSABLE else '用户记录'}")
                raise KeyError(f"{n!r} 不能这样删除：{why}。"
                               f"可用：{sorted(pool)}")
            targets.append(pool[n])
        return targets

    def clear(self, names=None) -> "ClearReport":
        """删除指定的**可再生**项。

        names=None 表示全部可再生项。传别的名字（如 "models" 或 "history"）
        会抛 KeyError —— 故意的：一个"清理缓存"按钮不该能删掉用户资产或记录。
        用户记录要用 purge()。
        """
        return self._delete(self._resolve(names, Retention.DISPOSABLE))

    def purge(self, names=None) -> "ClearReport":
        """删除指定的**用户记录**（历史）。

        与 clear() 分开是因为语义不同：这里删掉的**不可恢复**，调用方必须先
        向用户确认。机制（删-或-改名-延后清理）是共用的。
        """
        return self._delete(self._resolve(names, Retention.USER_RECORD))

    def _delete(self, targets) -> "ClearReport":
        """共用的删除机制。历史那一条委托给 store，别绕过它删文件。"""
        report = ClearReport()
        for item in targets:
            if item.name == "history" and self._history is not None:
                # 委托：历史的内存状态在 store 里，直接删文件会让它缓存失效
                try:
                    before = item.size
                    outcome = self._history.purge()
                except Exception as exc:
                    log.exception("history purge failed")
                    report.blocked.append(item.path)
                    continue
                if outcome:
                    report.cleared.append(item.name)
                    report.freed += before
                else:
                    report.blocked.append(item.path)
                continue

            freed, failures = _remove_tree(item.path)
            report.freed += freed
            report.cleared.append(item.name)
            for path in failures:
                # 删不掉（多半是被当前进程引用）：改名释放空间，下次启动再清。
                # _defer 返回**改名后**的路径 —— 空间要按新路径算，而且报告里
                # 必须给出真实存在的位置，否则用户按旧路径去找会找不到。
                moved = _defer(path)
                if moved:
                    report.deferred.append(moved)
                    report.freed += _size_of(moved)
                else:
                    report.blocked.append(path)
        self._inventory = None
        log.info("cache delete: cleared=%s freed=%d deferred=%d blocked=%d",
                 report.cleared, report.freed, len(report.deferred),
                 len(report.blocked))
        return report


class ClearReport:
    """一次清理的结果。名字都要能直接显示给用户。"""

    __slots__ = ("freed", "cleared", "deferred", "blocked")

    def __init__(self):
        self.freed = 0
        self.cleared: list[str] = []
        self.deferred: list[str] = []      # 被占用，已改名，下次启动清
        self.blocked: list[str] = []       # 既删不掉也改不了名

    def summary(self) -> str:
        parts = [f"释放 {_human(self.freed)}"]
        if self.deferred:
            parts.append(f"{len(self.deferred)} 个文件被占用，重启后自动清除")
        if self.blocked:
            parts.append(f"{len(self.blocked)} 个文件无法删除")
        return "，".join(parts)


# --- 内部实现（不属于接口） -------------------------------------------

def _measure(path: str) -> tuple[int, int]:
    """返回 (总字节, 文件数)。目录不存在按 0 处理，不抛异常。"""
    if not os.path.isdir(path):
        return 0, 0
    total = 0
    files = 0
    for root, _dirs, names in os.walk(path, onerror=lambda e: None):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
                files += 1
            except OSError:
                pass
    return total, files


def measure_path(path: str) -> int:
    """报告一项占多少字节。**文件也算** —— 历史就是一个文件，
    只用 _measure（只管目录）会把它报成 0，界面上看起来像没占空间。"""
    return _size_of(path)


def _size_of(path: str) -> int:
    if os.path.isdir(path):
        return _measure(path)[0]
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _remove_tree(path: str) -> tuple[int, list[str]]:
    """尽力删除。返回 (已释放字节, 删不掉的路径列表)。

    不用 shutil.rmtree(ignore_errors=True)：那样拿不到"哪些没删掉"，
    而我们需要据此决定改名还是上报。
    """
    if not os.path.exists(path):
        return 0, []
    freed = 0
    failures: list[str] = []

    if os.path.isfile(path):
        try:
            size = os.path.getsize(path)
            os.remove(path)
            return size, []
        except OSError:
            return 0, [path]

    for root, dirs, names in os.walk(path, topdown=False, onerror=lambda e: None):
        for n in names:
            f = os.path.join(root, n)
            try:
                size = os.path.getsize(f)
                os.remove(f)
                freed += size
            except OSError:
                failures.append(f)
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass
    return freed, failures


def _defer(path: str) -> str | None:
    """把删不掉的文件改名，先释放空间，留到下次启动清。

    成功返回**改名后**的路径，失败返回 None。返回新路径而不是 bool，
    是因为调用方需要它去统计大小、并让用户找得到文件。
    """
    target = f"{path}.trash-{int(_time.time())}"
    try:
        os.rename(path, target)
        return target
    except OSError:
        return None


def _human(n: int) -> str:
    step = 1024.0
    v = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < step or unit == "GB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= step
    return f"{v:.1f} GB"


# 进程内单例：磁盘数据的事实只该有一份，界面和自检都读它。
# ARGOS_DATA_DIR 由 app.py 顶部算出（它知道 XDG_DATA_HOME 的最终值），
# 这里只消费，不去猜。
# HISTORY 定义在下一段（翻译历史），所以这里先建、稍后回填 —— 见 set_history()。
CACHE = CacheManager(DATA_ROOT, CACHE_DIR, PACKAGES_DIR, LOG_DIR, ARGOS_DATA_DIR)
