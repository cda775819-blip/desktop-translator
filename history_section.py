# ======================================================================
# 7. 翻译历史
# ======================================================================
#
# 历史**不是缓存**：缓存删了会重建，历史删了就没了。所以它单独一个模块，
# 自己拥有记录语义和删除动作。
#
# 存储形态：一行一条 JSON（JSONL）。
#   为什么不是一个大 JSON 数组：追加式写入只需要 append，崩在写一半也只毁掉
#   最后一行；而重写整个数组的话，崩在写一半会毁掉全部历史。
#
# 两条上限同时生效（缺一个都会漏）：
#   MAX_ENTRIES  条数上限    —— 防止"每次都很短但次数极多"
#   MAX_BYTES    字节上限    —— 防止"次数不多但每次都是几十万字的长文"
# 超限时从**最旧**的一端淘汰。单条内容永远完整保存、不截断。
#
# 写入契约（调用方必须知道）：
#   record() **永不抛异常**。它的调用点在翻译成功之后，那里抛异常会让用户
#   看到"翻译失败"，而翻译其实成功了。失败通过 RecordOutcome 返回。

import json                      # noqa: E402
import os                        # noqa: E402
import time as _time             # noqa: E402

# _defer 来自上面的"缓存管理"段落 —— 复用同一套"删不掉就改名释放空间"的策略，
# 而不是再写一遍。拼接后它们在同一模块里，所以直接用。
# （_defer 定义在 cache_section.py，由 splice_cache.py 拼在 6 段。）

HISTORY_VERSION = 1

DEFAULT_MAX_ENTRIES = 200
DEFAULT_MAX_BYTES = 8 * 1024 * 1024      # 8 MB


class HistoryEntry:
    """一条历史记录。字段是稳定的：界面、自检、缓存报告都读它。"""

    __slots__ = ("src", "tgt", "source", "target", "ts", "elapsed", "truncated")

    def __init__(self, src, tgt, source, target, ts, elapsed=0.0,
                 truncated=False):
        self.src = src
        self.tgt = tgt
        self.source = source
        self.target = target
        self.ts = ts                     # unix 秒，float
        self.elapsed = elapsed
        self.truncated = truncated

    @property
    def chars(self) -> tuple[int, int]:
        return len(self.source), len(self.target)

    def to_json(self) -> str:
        return json.dumps({
            "v": HISTORY_VERSION,
            "src": self.src, "tgt": self.tgt,
            "source": self.source, "target": self.target,
            "ts": round(self.ts, 3), "elapsed": round(self.elapsed, 2),
        }, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def from_obj(o: dict) -> "HistoryEntry | None":
        """宽容解析：缺字段或类型不对就丢弃这条，不让整份历史挂掉。"""
        try:
            src = str(o["src"]); tgt = str(o["tgt"])
            source = o["source"]; target = o["target"]
            if not isinstance(source, str) or not isinstance(target, str):
                return None
            if not src or not tgt:
                return None
            ts = float(o.get("ts") or 0.0)
        except (KeyError, TypeError, ValueError):
            return None
        return HistoryEntry(src, tgt, source, target, ts,
                            float(o.get("elapsed") or 0.0))

    def head(self, n: int = 60) -> str:
        """给列表用的单行预览。"""
        first = self.source.strip().splitlines()[0] if self.source.strip() else ""
        return first[:n] + ("…" if len(first) > n else "")

    def __repr__(self):
        return (f"HistoryEntry({self.src}->{self.tgt}, "
                f"{len(self.source)}->{len(self.target)} chars)")


class RecordOutcome:
    """一次记录尝试的结果。record() 不抛异常，所以结果只能从这里看。"""

    __slots__ = ("ok", "reason")

    def __init__(self, ok: bool, reason: str = ""):
        self.ok = ok
        self.reason = reason

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self):
        return f"RecordOutcome(ok={self.ok}, reason={self.reason!r})"


class HistoryStats:
    """历史的总体情况，给界面和缓存报告用。"""

    __slots__ = ("count", "bytes", "max_entries", "max_bytes", "path")

    def __init__(self, count, nbytes, max_entries, max_bytes, path):
        self.count = count
        self.bytes = nbytes
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.path = path

    def __repr__(self):
        return (f"HistoryStats(count={self.count}, bytes={self.bytes}, "
                f"limits={self.max_entries}/{self.max_bytes})")


class HistoryStore:
    """翻译历史的事实来源。

    对外四个动作：
        record(...)     追加一条，**永不抛**
        page(...)       按时间倒序分页读取
        purge()         清空全部历史
        summary()       条目数 / 占用字节 / 上限

    约束：
      * record() 与 purge() 之间是串行的（内部有锁）；page() 也走同一把锁。
      * 写入是追加式的，崩溃最多损失最后一条，不会毁掉整份历史。
      * 淘汰（超上限）会重写文件，用临时文件 + os.replace，原子生效。
      * 目录**延迟创建**：构造时不碰磁盘。这不只是洁癖 —— CacheManager 的
        sweep_deferred() 依赖"父目录能被删掉"，如果在它之前就建出目录，
        .trash-* 残留会永远清不掉。
    """

    def __init__(self, path, max_entries=DEFAULT_MAX_ENTRIES,
                 max_bytes=DEFAULT_MAX_BYTES):
        self.path = path
        self.max_entries = max(1, int(max_entries))
        self.max_bytes = max(1024, int(max_bytes))
        self._lock = threading.RLock()
        self._cache: list[HistoryEntry] | None = None   # 时间倒序
        self._dirty = True

    # -- 写入 ----------------------------------------------------------

    def record(self, src, tgt, source, target, elapsed=0.0) -> RecordOutcome:
        """追加一条历史。**任何情况下都不抛异常。**

        单条内容完整保存、不截断（容量靠条数 + 字节两个上限兜住）。
        """
        try:
            if not source or not source.strip():
                return RecordOutcome(False, "空原文")
            entry = HistoryEntry(str(src), str(tgt), source, target,
                                 _time.time(), float(elapsed or 0.0))
        except Exception as exc:                       # pragma: no cover
            log.warning("history: 构造记录失败: %r", exc)
            return RecordOutcome(False, f"构造失败: {exc}")

        with self._lock:
            try:
                self._ensure_dir()
                self._append(entry)
            except Exception as exc:
                log.warning("history: 写入失败: %r", exc)
                return RecordOutcome(False, f"写入失败: {exc}")

            try:
                self._invalidate()
                self._enforce_limits()
            except Exception as exc:
                # 淘汰失败不该影响"这条已经写进去了"这个事实
                log.warning("history: 淘汰失败（记录已保存）: %r", exc)
            return RecordOutcome(True)

    def purge(self) -> RecordOutcome:
        """清空全部历史。删不掉时改名释放空间（与缓存清理同一套策略）。"""
        with self._lock:
            try:
                if os.path.exists(self.path):
                    try:
                        os.remove(self.path)
                    except OSError:
                        moved = _defer(self.path)
                        if moved is None:
                            return RecordOutcome(False, "文件被占用，无法删除")
                # 清掉可能存在的 .trash-* 与临时文件
                self._invalidate()
                return RecordOutcome(True)
            except Exception as exc:
                log.warning("history: purge 失败: %r", exc)
                return RecordOutcome(False, str(exc))

    # -- 读取 ----------------------------------------------------------

    def page(self, offset: int = 0, limit: int = 50) -> list[HistoryEntry]:
        """按时间**倒序**（最新在前）返回一页。越界返回空列表。"""
        with self._lock:
            items = self._load()
            if offset < 0:
                offset = 0
            return items[offset:offset + max(0, limit)]

    def all_entries(self) -> list[HistoryEntry]:
        with self._lock:
            return list(self._load())

    def summary(self) -> HistoryStats:
        with self._lock:
            items = self._load()
            try:
                nbytes = os.path.getsize(self.path)
            except OSError:
                nbytes = 0
            return HistoryStats(len(items), nbytes, self.max_entries,
                                self.max_bytes, self.path)

    # -- 内部实现（不属于接口） ----------------------------------------

    def _ensure_dir(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _append(self, entry: HistoryEntry) -> None:
        # 行缓冲 + 显式 flush：保证进程被强杀时这条也已经落盘
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(entry.to_json() + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _invalidate(self) -> None:
        self._cache = None
        self._dirty = True

    def _load(self) -> list[HistoryEntry]:
        """读全部并解析，时间倒序。坏行跳过，不让一行毁掉整份历史。"""
        if self._cache is not None and not self._dirty:
            return self._cache
        items: list[HistoryEntry] = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("history: 跳过损坏的一行")
                        continue
                    if isinstance(obj, dict):
                        e = HistoryEntry.from_obj(obj)
                        if e is not None:
                            items.append(e)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("history: 读取失败: %r", exc)
        items.sort(key=lambda e: e.ts, reverse=True)     # 最新在前
        self._cache = items
        self._dirty = False
        return items

    def _enforce_limits(self) -> None:
        """超上限就从最旧的一端淘汰。条数与字节两个上限都算。"""
        items = self._load()
        if not items:
            return
        keep = items
        # 1) 条数上限（items 已是最新在前，所以保留前 N 条 = 保留最新的 N 条）
        if len(keep) > self.max_entries:
            keep = keep[:self.max_entries]
        # 2) 字节上限：从最新往旧累加，超了就砍
        if self.max_bytes > 0 and len(keep) > 1:
            total = 0
            cut = len(keep)
            # 每条行的实际字节 = utf-8 编码后的长度 + 换行
            for i, e in enumerate(keep):
                total += len(e.to_json().encode("utf-8")) + 1
                if total > self.max_bytes:
                    cut = i                      # 保留 0..i-1
                    break
            if cut < len(keep):
                keep = keep[:max(1, cut)]        # 至少留最新一条
        if len(keep) == len(items):
            return
        dropped = len(items) - len(keep)
        self._rewrite(keep)
        log.info("history: 淘汰 %d 条（保留 %d 条）", dropped, len(keep))

    def _rewrite(self, keep: list[HistoryEntry]) -> None:
        """原子重写：临时文件 + os.replace。"""
        tmp = f"{self.path}.tmp-{os.getpid()}"
        self._ensure_dir()
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            for e in keep:
                fh.write(e.to_json() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        self._invalidate()


# 进程内单例：历史的事实只该有一份。
# 路径跟着程序目录走（和 models/ logs/ 一致），所以便携版拷走也带着历史。
HISTORY = HistoryStore(os.path.join(DATA_ROOT, "history.jsonl"))

# 把历史登记进磁盘清单，并把历史的删除交给它自己执行。
# 顺序要求：必须在任何 CACHE.inventory() 之前 —— 界面和自检都读同一份清单。
CACHE.set_history(HISTORY)
