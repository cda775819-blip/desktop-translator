#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌面翻译助手 (离线版)
=====================

基于 Argos Translate / CTranslate2 的纯离线桌面翻译工具，单文件、点开即用。

设计要点（都是踩过坑之后定下来的）：

1. **模型路径必须是纯英文**
   sentencepiece 的 C++ 库用窄字符 API 打开文件，路径含中文会直接
   `OSError: Not found`（实测 D:\\程序\\... 被读成 D:\\????\\...）。所以本程序
   只在英文路径下工作：优先用程序自己所在目录；如果该目录含非 ASCII 字符，
   自动改用 %LOCALAPPDATA%\\Translator。模型目录可以是指向任何地方的目录链接。

2. **启动要快**
   `import ctranslate2` 会经 converters 拉进 transformers+torch（实测 34s），
   `argostranslate.sbd` 会拉进 stanza+spacy（实测 14s）。两者翻译都用不到，
   已通过 site-packages 惰性导入补丁 + 本文件的兜底 shim 消除。

3. **不能设 ARGOS_CHUNK_TYPE=NONE**
   chunk_type 为 NONE 时 PackageTranslation 挑不到 Sentencizer，会抛
   NotImplementedError，而 get_installed_languages() 会把它当成坏包跳过 ——
   结果是 100 个包全被丢弃，任何翻译都返回 None。必须保持 DEFAULT。

4. **不联网也要能用**
   一律先查本地已安装模型；只有用户明确点了"下载"，或本地确实缺模型时
   才访问网络，并且带超时，不会默默卡住。

5. **Tk 不是线程安全的**
   所有耗时工作在线程里做，结果通过 queue 回主线程轮询消费，
   绝不在工作线程里碰控件。

配置文件: <程序目录>/config/settings.json   （含中文时退到 LOCALAPPDATA）
运行日志: <程序目录>/logs/app.log
模型目录: <程序目录>/models/packages
"""

from __future__ import annotations

import json
import os
import re
import re as _re
import sys


# ======================================================================
# 1. 环境准备 —— 必须放在任何重量级 import 之前
# ======================================================================

# ctranslate2 与 onnxruntime 各自带一份 libiomp5md.dll，同进程加载会触发
# OMP Error #15 直接终止进程。必须在 import numpy/ctranslate2 之前设置。
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "4")

# 离线优先：清掉系统代理，避免代理没开时请求挂在半路。
for _key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_key, None)

# 句边界检测用 argostranslate 默认的 MiniSBD 分句器（不要改成 NONE，见文件头说明）。
# MiniSBD 的 onnx 分句模型随程序预置，首次翻译无需联网。
os.environ.setdefault("ARGOS_CHUNK_TYPE", "DEFAULT")


# ======================================================================
# 2. 路径 —— 只允许 ASCII
# ======================================================================

def _app_dir() -> str:
    """打包后是 exe 所在目录；直接跑脚本时是脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _pick_data_root(candidate: str) -> str:
    """候选目录是纯 ASCII 就用它，否则退回 LOCALAPPDATA。"""
    try:
        candidate.encode("ascii")
    except UnicodeEncodeError:
        return os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Translator"
        )
    return candidate


APP_DIR = _app_dir()
DATA_ROOT = _pick_data_root(APP_DIR)
IS_PORTABLE = os.path.normcase(DATA_ROOT) == os.path.normcase(APP_DIR)


def _pick_cache_root() -> str:
    """MiniSBD 分句模型优先用随包预置的那份。

    _internal 目录不该被写入（也可能只读），所以打包后先看包里有没有
    cache/minisbd；没有才退回可写的程序目录。
    """
    bundled = os.path.join(getattr(sys, "_MEIPASS", "") or "", "cache", "minisbd")
    if os.path.isdir(bundled):
        return os.path.dirname(bundled)
    return os.path.join(DATA_ROOT, "cache")


CACHE_DIR = _pick_cache_root()
PACKAGES_DIR = os.path.join(DATA_ROOT, "models", "packages")
CONFIG_DIR = os.path.join(DATA_ROOT, "config")
LOG_DIR = os.path.join(DATA_ROOT, "logs")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
LOG_FILE = os.path.join(LOG_DIR, "app.log")

for _d in (PACKAGES_DIR, CONFIG_DIR, LOG_DIR, CACHE_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except OSError:
        pass

os.environ.setdefault("XDG_CACHE_HOME", CACHE_DIR)
os.environ.setdefault("XDG_DATA_HOME", os.path.join(DATA_ROOT, "data"))
# 这一行是**关键**，别删：argostranslate.settings 在 import 时读它来决定
# 去哪儿找已安装的模型包。少了它，程序会去看 XDG_DATA_HOME 下的默认位置
# （一个空目录），结果是 100 个模型一个都不认识 —— 实测踩过，
# 表现为"目录里有 100 个包，但 get_installed_packages() 返回 0"。
os.environ["ARGOS_PACKAGES_DIR"] = PACKAGES_DIR
# argostranslate 会把索引写到 XDG_DATA_HOME/argos-translate。
# 在这里算一次，缓存管理模块直接消费，不去猜环境变量（setdefault 之上
# 可能还有外部设的值）。
# 注意顺序：XDG_* 与 ARGOS_PACKAGES_DIR 都必须在上面的 import 之前生效。
ARGOS_DATA_DIR = os.path.join(os.environ["XDG_DATA_HOME"], "argos-translate")


# ======================================================================
# 3. 日志
# ======================================================================

import logging  # noqa: E402

log = logging.getLogger("translator")

try:
    _handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
except OSError:
    log.addHandler(logging.NullHandler())


# ======================================================================
# 4. 兜底 shim —— 万一 site-packages 补丁没打上/被覆盖
# ======================================================================

def _converters_is_lazy() -> bool:
    """检查 ctranslate2/converters/__init__.py 是不是已经改成惰性导入了。

    关键：**不能**用 importlib.util.find_spec("ctranslate2.converters")，
    那会先把父包 ctranslate2 导进来，而父包结尾就是 `from ctranslate2 import
    converters, ...` —— 等于自己把要躲的东西叫醒了。这里只做纯文件读取。
    """
    try:
        import importlib.util
        pkg = importlib.util.find_spec("ctranslate2")
        if not (pkg and pkg.submodule_search_locations):
            return False
        init = os.path.join(list(pkg.submodule_search_locations)[0],
                            "converters", "__init__.py")
        with open(init, "r", encoding="utf-8") as fh:
            src = fh.read()
    except Exception:
        return False
    if "from ctranslate2.converters.transformers import" not in src:
        return False
    return "try:" in src or "__getattr__" in src


def _install_fast_import_shim() -> None:
    """让 `import ctranslate2` 不去碰 converters -> transformers -> torch。

    converters 只在"把别的格式的模型转成 CTranslate2"时才用得到，翻译已转好的
    模型完全用不上。site-packages 里打了补丁就直接返回；万一补丁被覆盖了，
    就先塞一个占位模块顶住，并且**不再换回来**（换回来等于白省）。
    占位模块的 __getattr__ 会按需惰性导入真正的 converters，所以
    ctranslate2.converters.TransformersConverter 依然可用。
    """
    import types

    if "ctranslate2.converters" in sys.modules:
        return
    if _converters_is_lazy():
        log.info("ctranslate2.converters is lazy; no shim needed")
        return

    placeholder = types.ModuleType("ctranslate2.converters")
    placeholder.__path__ = []          # type: ignore[attr-defined]

    def _getattr(name):
        if name.startswith("_"):
            raise AttributeError(name)
        import importlib
        real = importlib.import_module("ctranslate2.converters")
        return getattr(real, name)

    placeholder.__getattr__ = _getattr    # type: ignore[attr-defined]
    sys.modules["ctranslate2.converters"] = placeholder
    log.info("fast-import shim installed (ctranslate2.converters deferred)")


_install_fast_import_shim()

import ctranslate2  # noqa: E402

# GPU 加速：只有 CTranslate2 真的看到 CUDA 设备才启用，
# 否则设了以后翻译阶段直接报错。
try:
    if ctranslate2.get_cuda_device_count() > 0:
        os.environ.setdefault("ARGOS_DEVICE_TYPE", "cuda")
        log.info("CUDA device detected, using GPU")
except Exception:
    pass


# ======================================================================
# 5. 引擎
# ======================================================================

import queue          # noqa: E402
import threading      # noqa: E402
import time           # noqa: E402
import traceback      # noqa: E402
import urllib.error   # noqa: E402

ENGINE_ERROR = ""
try:
    import argostranslate.package as argos_package
    import argostranslate.translate as argos_translate
    HAS_ENGINE = True
except Exception as _exc:                             # pragma: no cover
    HAS_ENGINE = False
    ENGINE_ERROR = str(_exc)
    log.exception("argostranslate import failed")

# langdetect 只是补充手段，装不上也不影响（本文件自带字符集启发式）
try:
    from langdetect import DetectorFactory, detect as _ld_detect
    DetectorFactory.seed = 0
    HAS_LANGDETECT = True
except Exception:
    HAS_LANGDETECT = False

# MiniSBD 的分句 onnx 模型默认落在 %LOCALAPPDATA%\Cache\minisbd，而且是
# "第一次用到才下载"。改到程序自己的目录，并让打包时预置好，
# 这样首次翻译不需要联网，也不用等下载。
MINISBD_DIR = os.path.join(CACHE_DIR, "minisbd")
HAS_MINISBD = False
try:
    os.makedirs(MINISBD_DIR, exist_ok=True)
    import minisbd as _minisbd
    import minisbd.models as _minisbd_models
    _minisbd_models.cache_dir = MINISBD_DIR
    _minisbd.models.cache_dir = MINISBD_DIR
    HAS_MINISBD = True
except Exception:
    log.warning("minisbd unavailable; sentence splitting will not be available")


class NoModelError(RuntimeError):
    """本地缺模型，且还没拉取过可下载清单。"""


class PackageMissingError(RuntimeError):
    """本地缺模型，且官方索引里也没有这个语言对。"""


class TranslationCancelled(RuntimeError):
    """用户点了取消。"""


# --- 分段 / 退化检测 --------------------------------------------------
# 句末标点：英文 . ! ? ; 和中文 。！？；，以及换行。用 lookbehind 保留标点。
_SENT_SPLIT = re.compile(r"(?<=[.!?;。！？；])\s+|\n+")


def split_for_translation(text: str, max_chars: int) -> list[str]:
    """按整句聚合到 max_chars 以内。

    整句为单位很重要：模型看到断在半截的句子会翻得更差，句子边界是天然的
    语义边界。两种特殊情况才硬切：
      * 单句本身就超长（没有标点的长串、URL 列表等）
      * 中日韩文本没有词间空格，靠空格拼接会一路拼到超限
    """
    max_chars = max(16, int(max_chars))
    pieces = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    if not pieces:
        pieces = [text.strip()] if text.strip() else []
    if not pieces:
        return []

    def piece_len(s: str) -> int:
        """计费长度：CJK 字符按 2 算，避免"看起来很短其实很长"。"""
        return sum(2 if ord(c) > 0x2E80 else 1 for c in s)

    out: list[str] = []
    buf = ""
    for p in pieces:
        # 单句超长：先冲刷缓冲，再把这句硬切成 max_chars 一片
        while piece_len(p) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            cut = max_chars
            acc = 0
            for i, c in enumerate(p):
                acc += 2 if ord(c) > 0x2E80 else 1
                if acc > max_chars:
                    cut = max(1, i)
                    break
            else:
                cut = len(p)
            out.append(p[:cut])
            p = p[cut:]
        if not p:
            continue
        # 中日韩不加空格，其它语言加空格
        sep = "" if (buf and ord(buf[-1]) > 0x2E80) else " "
        candidate = (buf + sep + p) if buf else p
        if piece_len(candidate) <= max_chars:
            buf = candidate
        else:
            if buf:
                out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    return out


def is_degenerate(text: str) -> bool:
    """检测神经翻译的"复读机"退化。

    实测：重复性输入（每句结构几乎一样）会让解码器卡在某个片段上反复吐，
    一直到撞上解码上限。自然文本不会误报。

    两个信号，命中任一即判定：
      1. 周期性重复——某个 2~15 字符的片段在窗口里占了绝大多数
      2. 单字符刷屏——同一个字占了窗口的很大比例（"站台"这种两字循环失效时兜底）
    单看尾部一个窗口不够：重复段未必正好落在最后 80 字符里，所以多取几个窗口。
    """
    if not text:
        return False
    n = len(text)
    if n < 24:
        return False

    def periodic(win: str) -> bool:
        """窗口尾部是否在重复某个片段。

        不设 span 上限，用"最小周期"一把算出来：从尾部开始找最短的、能完整
        平铺到窗口大部分位置的重复单元。
        """
        w = len(win)
        if w < 24:
            return False
        for span in range(2, w // 3 + 1):
            unit = win[-span:]
            if not unit.strip():
                continue
            reps = 0
            pos = w
            while pos - span >= 0 and win[pos - span:pos] == unit:
                reps += 1
                pos -= span
            if reps >= 4 and reps * span >= w * 0.6:
                return True
        return False

    def one_char_flood(win: str) -> bool:
        w = len(win)
        if w < 24:
            return False
        counts: dict[str, int] = {}
        for ch in win:
            counts[ch] = counts.get(ch, 0) + 1
        top = max(counts.values())
        return top / w >= 0.45

    # 尾部多个窗口 + 全文窗口
    for size in (60, 120, 240):
        win = text[-size:]
        if periodic(win) or one_char_flood(win):
            return True
    # 输出比输入长很多时，重复可能在中段：抽查最长输出的一半
    if n >= 600:
        mid = text[n // 2: n // 2 + 240]
        if periodic(mid) or one_char_flood(mid):
            return True
    return False


# --- 语言表 -----------------------------------------------------------
# 代码用 Argos 自己的（注意：挪威语是 nb 不是 no，巴西葡语是 pb 不是 pt-BR）
_LANG_ROWS = [
    ("zh", "简体中文"), ("zt", "繁體中文"), ("en", "English"),
    ("ja", "日本語"), ("ko", "한국어"), ("fr", "Français"),
    ("de", "Deutsch"), ("es", "Español"), ("pt", "Português"),
    ("pb", "Português (Brasil)"), ("it", "Italiano"), ("ru", "Русский"),
    ("ar", "العربية"), ("nl", "Nederlands"), ("pl", "Polski"),
    ("tr", "Türkçe"), ("vi", "Tiếng Việt"), ("th", "ไทย"),
    ("id", "Bahasa Indonesia"), ("hi", "हिन्दी"), ("bn", "বাংলা"),
    ("ur", "اردو"), ("fa", "فارسی"), ("he", "עברית"),
    ("sv", "Svenska"), ("da", "Dansk"), ("fi", "Suomi"),
    ("nb", "Norsk bokmål"), ("el", "Ελληνικά"), ("cs", "Čeština"),
    ("hu", "Magyar"), ("ro", "Română"), ("uk", "Українська"),
    ("bg", "Български"), ("ca", "Català"), ("sk", "Slovenčina"),
    ("sl", "Slovenščina"), ("lt", "Lietuvių"), ("lv", "Latviešu"),
    ("et", "Eesti"), ("sq", "Shqip"), ("ga", "Gaeilge"),
    ("gl", "Galego"), ("eu", "Euskara"), ("eo", "Esperanto"),
    ("az", "Azərbaycan"), ("ky", "Кыргызча"), ("sw", "Kiswahili"),
    ("tl", "Tagalog"), ("ms", "Bahasa Melayu"),
]
LANG_NAMES = dict(_LANG_ROWS)
LANG_CODES = [c for c, _ in _LANG_ROWS]

DETECT_FIXUPS = {
    "zh-cn": "zh", "zh-tw": "zt", "zh-hans": "zh", "zh-hant": "zt",
    "jp": "ja", "kr": "ko", "nn": "nb", "no": "nb", "nb": "nb",
    "pt-br": "pb", "pt-pt": "pt", "iw": "he", "in": "id", "fil": "tl",
    "mo": "ro",
}

# --- 语言检测：先用字符集，秒出；拿不准再问 langdetect ---------------
_SCRIPT_RANGES = (
    ("ja", ((0x3040, 0x30FF), (0x31F0, 0x31FF))),   # 假名
    ("ko", ((0xAC00, 0xD7AF), (0x1100, 0x11FF))),   # 谚文
    ("zh", ((0x4E00, 0x9FFF), (0x3400, 0x4DBF))),   # 汉字
    ("ru", ((0x0400, 0x04FF),)),                    # 西里尔
    ("ar", ((0x0600, 0x06FF), (0x0750, 0x077F))),   # 阿拉伯
    ("he", ((0x0590, 0x05FF),)),                    # 希伯来
    ("th", ((0x0E00, 0x0E7F),)),                    # 泰文
    ("el", ((0x0370, 0x03FF),)),                    # 希腊
    ("hi", ((0x0900, 0x097F),)),                    # 天城文
    ("bn", ((0x0980, 0x09FF),)),                    # 孟加拉
    ("ur", ((0xFB50, 0xFDFF), (0xFE70, 0xFEFF))),   # 阿拉伯补充
)


def detect_language(text: str) -> str:
    """返回最可能的源语言代码。同步、很快，主线程可调用。"""
    text = text.strip()
    if not text:
        return "en"

    counts: dict[str, int] = {}
    latin = 0
    total = 0
    for ch in text:
        cp = ord(ch)
        if cp < 0x80:
            if ch.isalpha():
                latin += 1
                total += 1
            continue
        total += 1
        for code, ranges in _SCRIPT_RANGES:
            if any(lo <= cp <= hi for lo, hi in ranges):
                counts[code] = counts.get(code, 0) + 1
                break

    if total:
        # 假名出现基本就是日语（纯汉字会被判成中文）
        if counts.get("ja", 0) >= 1:
            return "ja"
        if counts:
            code, hits = max(counts.items(), key=lambda kv: kv[1])
            if hits / total >= 0.30:
                return code

    if latin and total and latin / total > 0.85:
        # 纯拉丁文本靠字符集分不出来，交给 langdetect
        if len(text) >= 12 and HAS_LANGDETECT:
            try:
                raw = _ld_detect(text)
            except Exception:
                return "en"
            if raw in LANG_CODES:
                return raw
            mapped = DETECT_FIXUPS.get(raw.lower())
            if mapped in LANG_CODES:
                return mapped
        return "en"

    if counts:
        return max(counts.items(), key=lambda kv: kv[1])[0]
    return "en"


# --- 引擎本体 ---------------------------------------------------------

class Engine:
    """封装 argostranslate：模型发现、按需下载、线程安全的翻译。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._installed: set[tuple[str, str]] = set()
        self._scanned = False
        self._available: list | None = None
        self._translations: dict[tuple[str, str], object] = {}

    # -- 已安装模型 ----------------------------------------------------
    def _rescan(self) -> set[tuple[str, str]]:
        """重建已安装集合，并让 argostranslate 的语言图跟着刷新。

        get_installed_languages() 上挂了 functools.lru_cache，装了新包以后
        必须 cache_clear()，否则新模型永远不出现在语言图里。
        """
        found: set[tuple[str, str]] = set()
        if HAS_ENGINE:
            try:
                for pkg in argos_package.get_installed_packages():
                    if getattr(pkg, "type", "translate") == "translate":
                        found.add((pkg.from_code, pkg.to_code))
            except Exception:
                log.exception("get_installed_packages failed")
            try:
                argos_translate.get_installed_languages.cache_clear()
            except Exception:
                log.exception("cache_clear failed")
        self._translations.clear()
        self._installed = found
        self._scanned = True
        return found

    def _scan_installed(self, force: bool = False) -> set[tuple[str, str]]:
        with self._lock:
            if self._scanned and not force:
                return self._installed
            return self._rescan()

    def installed_pairs(self) -> set[tuple[str, str]]:
        return set(self._scan_installed())

    def installed_count(self) -> int:
        return len(self._scan_installed())

    # -- 翻译对象 ------------------------------------------------------
    def _translation(self, src: str, tgt: str):
        """带缓存的 get_translation_from_codes，找不到返回 None。

        只缓存"成功拿到对象"的结果。失败（None）不入缓存 —— 否则一次瞬时
        故障会让这个语言对在本进程内**永久**不可用，除非重启程序。
        """
        key = (src, tgt)
        with self._lock:
            if key in self._translations:
                return self._translations[key]
        if not HAS_ENGINE:
            return None
        try:
            obj = argos_translate.get_translation_from_codes(src, tgt)
        except Exception:
            log.exception("get_translation_from_codes(%s,%s) failed", src, tgt)
            return None                       # 不缓存失败
        if obj is None:
            return None                       # 语言图里确实没有，下次还会再问
        with self._lock:
            self._translations[key] = obj
        return obj

    # -- 语言对可用性 --------------------------------------------------
    def _route(self, src: str, tgt: str, pairs: set[tuple[str, str]]) -> list[str]:
        """返回需要依次翻译的语言链；空表示走不通。"""
        if src == tgt:
            return [src]
        if (src, tgt) in pairs:
            return [src, tgt]
        if (src, "en") in pairs and ("en", tgt) in pairs:
            return [src, "en", tgt]          # 经英文中转
        return []

    def can_translate(self, src: str, tgt: str) -> bool:
        return bool(self._route(src, tgt, self._scan_installed()))

    def missing_links(self, src: str, tgt: str) -> list[tuple[str, str]]:
        """为了能翻译 src→tgt，本地还缺哪些模型。"""
        pairs = self._scan_installed()
        if self._route(src, tgt, pairs):
            return []
        need: list[tuple[str, str]] = []
        if src != "en" and (src, "en") not in pairs:
            need.append((src, "en"))
        if tgt != "en" and ("en", tgt) not in pairs:
            need.append(("en", tgt))
        return need

    # -- 可下载清单（需要联网，只在用户要求时调用）---------------------
    def refresh_available(self, progress=None) -> int:
        """拉取可下载清单。

        必须校验"拉到了东西"：update_package_index() 在失败时是**静默**的
        （下载出错只写日志、不抛异常）。如果不检查，断网时会把一个空清单
        当成有效清单缓存下来 —— 之后 _find_available 永远返回 None，
        用户看到的是"官方索引里没有 en → zh"，而真实原因是网络挂了。
        """
        if not HAS_ENGINE:
            raise RuntimeError("翻译引擎未安装")
        if progress:
            progress("正在获取模型清单…")
        argos_package.update_package_index()
        available = list(argos_package.get_available_packages())
        if not available:
            raise NoModelError(
                "获取到的模型清单是空的，通常表示网络不通或索引源不可用。\n"
                "本次结果不会被缓存，请检查网络后重试。"
            )
        with self._lock:
            self._available = available
            return len(self._available)

    def _find_available(self, src: str, tgt: str):
        with self._lock:
            avail = self._available
        if avail is None:
            raise NoModelError(
                "本地缺少该语言对的模型。\n"
                "请在「管理模型」里点「刷新可下载清单」后重试。"
            )
        for want in ((src, tgt), (tgt, src)):     # 少数语言对只有反向模型
            for p in avail:
                if (p.from_code == want[0] and p.to_code == want[1]
                        and getattr(p, "type", "translate") == "translate"):
                    return p
        return None

    def install_pair(self, src: str, tgt: str, progress=None) -> None:
        pkg = self._find_available(src, tgt)
        if pkg is None:
            raise PackageMissingError(
                f"官方索引里没有 {LANG_NAMES.get(src, src)} → "
                f"{LANG_NAMES.get(tgt, tgt)} 的模型。"
            )
        if progress:
            progress(f"正在下载 {src} → {tgt} …（几十到几百 MB，请稍候）")
        pkg.install()
        self._scan_installed(force=True)
        if progress:
            progress(f"{src} → {tgt} 安装完成")

    def ensure_ready(self, src: str, tgt: str, progress=None) -> None:
        """缺什么补什么；网络不通时给出明确提示而不是干等。"""
        need = self.missing_links(src, tgt)
        if not need:
            return
        with self._lock:
            have_index = self._available is not None
        if not have_index:
            try:
                self.refresh_available(progress)
            except (urllib.error.URLError, OSError) as exc:
                raise NoModelError(
                    f"本地缺模型，且无法连接模型索引：{exc}\n"
                    "请检查网络后重试，或在「管理模型」中手动下载。"
                ) from exc
            except Exception as exc:
                raise NoModelError(f"本地缺模型，获取模型清单失败：{exc}") from exc
        for pair in need:
            if pair not in self._scan_installed():
                self.install_pair(pair[0], pair[1], progress)

    # -- 翻译 ----------------------------------------------------------
    def translate(self, text: str, src: str, tgt: str, progress=None,
                  cancel: "threading.Event | None" = None) -> str:
        """分段翻译整篇文本。

        为什么要分段：这个 CTranslate2 模型单次输入有上限（实测 ~512 token，
        800 字符英文约 172 token，安全），超了会截断；而且神经网络翻译在
        重复性文本上会陷入"复读机"退化，输出一段永远停不下来。所以：

          1. 先按句子切成 <= CHUNK_CHARS 的块（整句为单位，不切断句子）
          2. 逐块翻译，块之间顺序调用（批量反而更慢：算力瓶颈，不是调度瓶颈）
          3. 某块输出疑似退化/为空/异常时，把它对半再切重试，最多递归到单句
          4. 单句都救不回来就保留原文并记录，最后统一提示，不静默丢内容
        """
        if not HAS_ENGINE:
            raise RuntimeError(f"翻译引擎不可用：{ENGINE_ERROR}")
        text = text.strip()
        if not text:
            return ""
        if src == tgt:
            return text

        def say(msg: str) -> None:
            if progress:
                try:
                    progress(msg)
                except Exception:
                    pass

        self.ensure_ready(src, tgt, progress)

        direct = self._translation(src, tgt)
        legs = None
        if direct is not None:
            legs = [(src, tgt, direct)]
        else:
            leg1 = self._translation(src, "en")
            leg2 = self._translation("en", tgt)
            if leg1 is not None and leg2 is not None:
                legs = [(src, "en", leg1), ("en", tgt, leg2)]
        if not legs:
            raise PackageMissingError(
                f"没有可用的 {LANG_NAMES.get(src, src)} → "
                f"{LANG_NAMES.get(tgt, tgt)} 翻译路径。"
            )

        # 每一段语言链都过一遍分段翻译。
        # 进度按"块"报，而不是按语言链——长文才看得出进展。
        pieces = [text]
        n_legs = len(legs)
        for leg_idx, (a, b, trans) in enumerate(legs, 1):
            chunks: list[str] = []
            for piece in pieces:
                chunks.extend(split_for_translation(piece, self.CHUNK_CHARS)
                              or [piece])
            if not chunks:
                chunks = [text]

            out = []
            prefix = f"[{leg_idx}/{n_legs}] " if n_legs > 1 else ""
            for i, ch in enumerate(chunks, 1):
                if cancel is not None and cancel.is_set():
                    raise TranslationCancelled("已取消")
                if len(chunks) > 1:
                    say(f"{prefix}翻译中… {i}/{len(chunks)}")
                out.append(self._translate_piece(trans, ch, 0, cancel))
            pieces = out
        return "\n".join(pieces) if len(pieces) > 1 else pieces[0]

    # 单块字符上限。800 字符英文实测最大 172 token，离模型 ~512 上限有余量；
    # 再大虽然单个块更快，但退化风险和单次等待都变差。
    CHUNK_CHARS = 800

    def _translate_piece(self, trans, text: str, depth: int = 0,
                         cancel: "threading.Event | None" = None) -> str:
        """翻译一段文本，遇到退化/空结果就细分重试。"""
        text = text.strip()
        if not text:
            return ""
        if cancel is not None and cancel.is_set():
            raise TranslationCancelled("已取消")

        # 短文本（一句话以内）没有细分余地，直接翻
        if len(text) <= self.CHUNK_CHARS or depth >= 6:
            return self._translate_one(trans, text, cancel)

        chunks = split_for_translation(text, self.CHUNK_CHARS)
        if len(chunks) <= 1:
            return self._translate_one(trans, text, cancel)

        out: list[str] = []
        for ch in chunks:
            out.append(self._translate_piece(trans, ch, depth + 1, cancel))
        return "\n".join(p for p in out if p)

    def _translate_one(self, trans, text: str,
                       cancel: "threading.Event | None" = None) -> str:
        """真正调用一次模型，附带退化检测和一次降级重试。"""
        if cancel is not None and cancel.is_set():
            raise TranslationCancelled("已取消")
        try:
            result = trans.translate(text)
        except TranslationCancelled:
            raise                                     # 取消不能被当成模型错误
        except Exception:
            log.exception("model call failed on a %d-char piece", len(text))
            raise
        if is_degenerate(result):
            # 复读机退化：把这段再切小重来一次（只重试一层，避免死循环）
            smaller = split_for_translation(text, max(80, len(text) // 3))
            if len(smaller) > 1:
                log.warning("degenerate output (%d chars in, %d out), retrying in "
                            "%d smaller pieces", len(text), len(result),
                            len(smaller))
                retried = [self._translate_one(trans, s, cancel) for s in smaller]
                joined = "\n".join(p for p in retried if p)
                if joined and not is_degenerate(joined):
                    return joined
            log.warning("degenerate output not recoverable for a %d-char piece",
                        len(text))
        return result


ENGINE = Engine()


# ======================================================================
# 6. 设置
# ======================================================================

DEFAULTS = {
    "src_lang": "auto",
    "tgt_lang": "zh",
    "topmost": True,
    "geometry": "",
}


def load_settings() -> dict:
    data = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            data.update(loaded)
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("settings load failed; using defaults")
    return data


def save_settings(data: dict) -> None:
    try:
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    except Exception:
        log.exception("settings save failed")


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


# ======================================================================
# 8. 翻译历史
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


# ======================================================================
# 9. 界面
# ======================================================================
#
# Tk 的控件本身很朴素，所以观感靠这几件事撑起来：
#   * 卡片化：内容区放在带 1px 描边的圆角观感面板里，而不是裸文本框
#   * 明确的层级：强调色只给主操作（翻译）和结果文本
#   * 悬停反馈：所有可点元素都有 hover 态，避免"不知道能不能点"
#   * DPI 感知：高分屏下不开 DPI 感知会被系统拉伸成糊的
#   * 图标：exe/快捷方式/窗口/任务栏统一用同一枚 .ico

import tkinter as tk                              # noqa: E402
from tkinter import messagebox, ttk               # noqa: E402

import re                                         # noqa: E402


def _enable_dpi_awareness() -> bool:
    """尽量开启 DPI 感知。必须在 Tk 创建窗口之前调用。

    高分屏上如果不声明感知，Windows 会把整个窗口位图拉伸，字会糊。
    返回是否成功 —— 这决定了 Tk 报告的屏幕尺寸是物理像素还是逻辑像素。
    """
    import ctypes
    if os.name != "nt":
        return False
    try:                                          # Win10 1703+
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(-4)):             # PER_MONITOR_AWARE_V2
            log.info("DPI awareness: per-monitor v2")
            return True
    except Exception:
        pass
    try:                                          # Win8.1+
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        log.info("DPI awareness: per-monitor")
        return True
    except Exception:
        pass
    try:                                          # Vista+
        ctypes.windll.user32.SetProcessDPIAware()
        log.info("DPI awareness: system")
        return True
    except Exception:
        pass
    log.info("DPI awareness: unavailable")
    return False


def _system_dpi_scale() -> float:
    """系统 DPI 缩放系数（1.0 = 100%）。用注册表读，不依赖进程 DPI 感知状态。"""
    if os.name != "nt":
        return 1.0
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Control Panel\Desktop\WindowMetrics")
        try:
            # AppliedDPI 存在时最准
            val, _ = winreg.QueryValueEx(key, "AppliedDPI")
            return max(1.0, float(val) / 96.0)
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass
    try:
        import ctypes
        dc = ctypes.windll.user32.GetDC(0)
        try:
            dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)   # LOGPIXELSX
        finally:
            ctypes.windll.user32.ReleaseDC(0, dc)
        return max(1.0, float(dpi) / 96.0) if dpi else 1.0
    except Exception:
        return 1.0


def _screen_logical_size(root) -> tuple[int, int, float]:
    """返回当前屏幕的**逻辑**宽高以及换算用的缩放系数。

    Tk 报告的是物理像素还是逻辑像素取决于进程的 DPI 感知状态：
      * 感知成功 -> winfo_screenwidth 给物理像素，需要除以缩放才是可用逻辑空间
      * 感知失败 -> Windows 已经帮忙虚拟化过，winfo_screenwidth 就是逻辑像素
    两种情况都要让"窗口宽度不超过可用空间"，否则窗口会伸到屏幕外面，
    用户看到的就是"右半截不见了"。
    """
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    scale = _system_dpi_scale()
    if _DPI_AWARE_OK and scale > 1.0:
        return max(640, int(sw / scale)), max(480, int(sh / scale)), scale
    return sw, sh, scale


def _set_app_user_model_id() -> None:
    """给进程一个独立 AppUserModelID，任务栏才会用自己的图标而不是 Python 的。"""
    import ctypes
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "ArgosTranslate.DesktopTranslator.Offline.1")
    except Exception:
        log.info("SetCurrentProcessExplicitAppUserModelID failed")


def _icon_path() -> str:
    base = os.path.join(APP_DIR, "assets", "app.ico")
    if os.path.exists(base):
        return base
    bundled = os.path.join(getattr(sys, "_MEIPASS", "") or "", "assets", "app.ico")
    return bundled if os.path.exists(bundled) else ""


def _pick_font(root: tk.Tk) -> str:
    """挑一个屏幕上真的存在、且中英都好看的字族。"""
    from tkinter import font as tkfont
    available = set(tkfont.families(root))
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI",
                 "PingFang SC", "Noto Sans CJK SC", "SimHei"):
        if name in available:
            return name
    return "TkDefaultFont"


def _fmt_size(n: int) -> str:
    """把字节数变成人能读的大小。"""
    v = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} GB"


# --- 配色：偏冷的深色，紫蓝强调 ---------------------------------------
THEME = {
    "bg":        "#0F1115",
    "surface":   "#171A21",
    "surface2":  "#1E222B",
    "border":    "#272C37",
    "border_hi": "#39404F",
    "fg":        "#E6E9EF",
    "fg_dim":    "#98A2B3",
    "fg_mute":   "#5C6675",
    "accent":    "#7C8CF8",
    "accent_hi": "#98A6FF",
    "accent_dk": "#5B6BE0",
    "good":      "#4ADE80",
    "warn":      "#FBBF24",
    "bad":       "#F87171",
    "result":    "#DCE4FF",
}


class Tooltip:
    """轻量 tooltip：悬停约半秒后在指针下方浮出说明。"""

    def __init__(self, widget, text: str, font, delay: int = 500):
        self.widget = widget
        self.text = text
        self.font = font
        self.delay = delay
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show(self):
        if self._tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 4
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.attributes("-topmost", True)
        tk.Label(self._tip, text=self.text, font=self.font,
                 bg=THEME["surface2"], fg=THEME["fg"],
                 bd=0, padx=8, pady=4).pack()
        self._tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, _event=None):
        self._cancel()
        if self._tip:
            self._tip.destroy()
            self._tip = None


class TranslateApp:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.results: queue.Queue = queue.Queue()
        self._busy = False
        self._after_id = None
        self._detect_gen = 0
        self._has_placeholder = False
        self._tooltips: list[Tooltip] = []

        self.root = tk.Tk()
        self.root.title("桌面翻译助手")
        self.root.configure(bg=THEME["bg"])

        # 初始尺寸必须按屏幕算，不能写死。
        # 之前写死 1040x700，配上一行放不下的顶部区域，窄屏上右半截直接被推到
        # 屏幕外，看起来就像"只有左边能用"。
        sw, sh, local_scale = _screen_logical_size(self.root)
        log.info("usable screen: %dx%d (dpi scale %.2f)", sw, sh, local_scale)
        win_w = max(760, min(1280, int(sw * 0.86)))
        win_h = max(520, min(820, int((sh - 60) * 0.92)))
        self.root.minsize(760, 520)
        self.root.geometry(f"{win_w}x{win_h}+{max(0, (sw - win_w) // 2)}"
                           f"+{max(0, (sh - win_h) // 2 - 15)}")

        # 有保存过的窗口位置就沿用 —— 但尺寸一律夹到当前可用空间内。
        # 只判"是否在屏幕内"是不够的：换显示器、改分辨率、或旧版本存过一个
        # 偏大的尺寸时，窗口会比屏幕还宽，右半截照样看不到。
        saved = self.settings.get("geometry") or ""
        m = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", saved)
        if m:
            gw, gh, gx, gy = (int(v) for v in m.groups())
            cw = max(760, min(gw, sw - 40))
            ch = max(520, min(gh, sh - 60))
            cx = max(0, min(gx, sw - cw))
            cy = max(0, min(gy, sh - ch))
            if (cw, ch, cx, cy) != (gw, gh, gx, gy):
                log.info("clamped saved geometry %s -> %dx%d+%d+%d",
                         saved, cw, ch, cx, cy)
            self.root.geometry(f"{cw}x{ch}+{cx}+{cy}")
        elif saved:
            log.warning("ignoring unparsable geometry %r", saved)

        self.root.attributes("-topmost", bool(self.settings.get("topmost", True)))

        # 记一笔实际尺寸，排查"窗口跑出屏幕"类问题时不用猜
        self.root.after(300, self._log_geometry)

        _set_app_user_model_id()
        ico = _icon_path()
        if ico:
            try:
                self.root.iconbitmap(default=ico)
            except tk.TclError:
                log.warning("iconbitmap failed for %s", ico)

        self.family = _pick_font(self.root)
        self.f_tiny = (self.family, 8)
        self.f_small = (self.family, 9)
        self.f_body = (self.family, 11)
        self.f_title = (self.family, 13, "bold")
        self.f_btn = (self.family, 11, "bold")
        self.f_mono = ("Consolas", 9)

        # 显示器数字要跟着 DPI：4K 高分屏上 11pt 太小，低分屏上又偏大
        if local_scale >= 1.5:
            self.f_body = (self.family, 12)
            self.f_small = (self.family, 10)
            self.f_tiny = (self.family, 9)
            self.f_title = (self.family, 14, "bold")

        self._init_styles()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(60, self._pump)
        self._start_warmup()

    # ---------------- ttk 主题 ----------------
    def _init_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "App.TCombobox",
            fieldbackground=THEME["surface2"],
            background=THEME["surface2"],
            foreground=THEME["fg"],
            arrowcolor=THEME["fg_dim"],
            bordercolor=THEME["border"],
            lightcolor=THEME["border"],
            darkcolor=THEME["border"],
            relief="flat",
            padding=(8, 5),
        )
        style.map(
            "App.TCombobox",
            fieldbackground=[("readonly", THEME["surface2"])],
            foreground=[("readonly", THEME["fg"])],
            bordercolor=[("focus", THEME["accent"]), ("hover", THEME["border_hi"])],
            arrowcolor=[("hover", THEME["accent"])],
        )
        # combobox 下拉列表的配色（Tk 的 popdown 是 Listbox，走 option 数据库）
        self.root.option_add("*TCombobox*Listbox.background", THEME["surface2"])
        self.root.option_add("*TCombobox*Listbox.foreground", THEME["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", THEME["accent_dk"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)

        style.configure("Card.Treeview",
                        background=THEME["surface2"],
                        fieldbackground=THEME["surface2"],
                        foreground=THEME["fg"],
                        bordercolor=THEME["border"],
                        rowheight=24,
                        font=self.f_small)
        style.configure("Card.Treeview.Heading",
                        background=THEME["surface"],
                        foreground=THEME["fg_dim"],
                        relief="flat",
                        font=self.f_small)
        style.map("Card.Treeview",
                  background=[("selected", THEME["accent_dk"])],
                  foreground=[("selected", "#FFFFFF")])
        style.map("Card.Treeview.Heading",
                  background=[("active", THEME["surface2"])])

        style.configure("Vertical.TScrollbar",
                        background=THEME["surface2"],
                        troughcolor=THEME["bg"],
                        bordercolor=THEME["bg"],
                        arrowcolor=THEME["fg_mute"],
                        relief="flat")
        style.map("Vertical.TScrollbar",
                  background=[("active", THEME["border_hi"])])

    # ---------------- 小组件工厂 ----------------
    def _tip(self, widget, text: str) -> None:
        self._tooltips.append(Tooltip(widget, text, self.f_tiny))

    def _hover_label(self, parent, text, command, *, fg=None, bg=None,
                     font=None, pad=(10, 5), tip=None, hover_bg=None):
        """可点标签：带 hover 变色，观感上像按钮但没有原生边框。"""
        fg = fg or THEME["accent"]
        bg = bg or THEME["bg"]
        font = font or self.f_small
        hover_bg = hover_bg or THEME["surface2"]
        lbl = tk.Label(parent, text=text, fg=fg, bg=bg, font=font,
                       cursor="hand2", padx=pad[0], pady=pad[1])
        lbl.bind("<Enter>", lambda e: lbl.config(bg=hover_bg, fg=THEME["accent_hi"]))
        lbl.bind("<Leave>", lambda e: lbl.config(bg=bg, fg=fg))
        lbl.bind("<Button-1>", lambda e: command())
        if tip:
            self._tip(lbl, tip)
        return lbl

    def _button(self, parent, text, command, *, kind="normal", width=None,
                font=None, tip=None):
        """两种层级的按钮：primary 用强调色实心，normal 用描边风格。"""
        if kind == "primary":
            bg, fg, hov = THEME["accent"], "#FFFFFF", THEME["accent_hi"]
            abg, afg = THEME["accent_dk"], "#FFFFFF"
            bd, relief = 0, tk.FLAT
        else:
            bg, fg, hov = THEME["surface2"], THEME["fg"], THEME["border_hi"]
            abg, afg = THEME["border_hi"], THEME["fg"]
            bd, relief = 0, tk.FLAT
        btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                        activebackground=abg, activeforeground=afg,
                        font=font or self.f_small, bd=bd, relief=relief,
                        cursor="hand2", padx=14, pady=6,
                        highlightthickness=0)
        if width:
            btn.config(width=width)
        btn.bind("<Enter>", lambda e: btn.config(bg=hov))
        btn.bind("<Leave>", lambda e: btn.config(bg=bg))
        if tip:
            self._tip(btn, tip)
        return btn

    def _card(self, parent, *, side=None, fill=tk.BOTH, expand=True,
              padx=(0, 0), pady=(0, 0)) -> tk.Frame:
        """带 1px 描边的卡片：外面一层当边框，里面一层当内容。"""
        outer = tk.Frame(parent, bg=THEME["border"], bd=0, highlightthickness=0)
        if side:
            outer.pack(side=side, fill=fill, expand=expand, padx=padx, pady=pady)
        inner = tk.Frame(outer, bg=THEME["surface"], bd=0, highlightthickness=0)
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        return inner

    # ---------------- 界面 ----------------
    def _build_ui(self) -> None:
        self._build_header()

        body = tk.Frame(self.root, bg=THEME["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=16, pady=(12, 0))
        self._build_input_card(body)
        self._build_output_card(body)

        self._build_footer()
        self._bind_keys()

    # ---- 顶部：品牌 + 语言选择 + 主操作 ----
    # 一行放不下时（窄窗口）自动折成两行：上行品牌+按钮，下行语言选择。
    # 之前写死一行，760px 宽时语言选择器会和按钮重叠。
    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=THEME["bg"])
        header.pack(fill=tk.X, padx=16, pady=(14, 0))
        self._header = header

        self._brand_row = tk.Frame(header, bg=THEME["bg"])
        self._brand_row.pack(fill=tk.X, side=tk.TOP)

        brand = tk.Frame(self._brand_row, bg=THEME["bg"])
        brand.pack(side=tk.LEFT)

        mark = tk.Canvas(brand, width=34, height=34, bg=THEME["bg"],
                         highlightthickness=0, bd=0)
        mark.pack(side=tk.LEFT)
        self._draw_mark(mark)

        titles = tk.Frame(brand, bg=THEME["bg"])
        titles.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(titles, text="桌面翻译助手", fg=THEME["fg"], bg=THEME["bg"],
                 font=self.f_title).pack(anchor=tk.W)
        self.sub_label = tk.Label(titles, text=self._subtitle(), fg=THEME["fg_mute"],
                                  bg=THEME["bg"], font=self.f_tiny)
        self.sub_label.pack(anchor=tk.W)

        # 主操作
        self.cancel_btn = self._button(self._brand_row, "取消", self._cancel_translate,
                                       tip="中止当前翻译")
        self.translate_btn = self._button(
            self._brand_row, "译  Translate", self._translate, kind="primary",
            font=self.f_btn, tip="Ctrl+Enter / F5")
        self.translate_btn.pack(side=tk.RIGHT, padx=(10, 0))

        self._picker = tk.Frame(header, bg=THEME["bg"])

        src_box = tk.Frame(self._picker, bg=THEME["bg"])
        src_box.pack(side=tk.LEFT)
        tk.Label(src_box, text="源语言", fg=THEME["fg_mute"], bg=THEME["bg"],
                 font=self.f_tiny).pack(anchor=tk.W)
        self.src_var = tk.StringVar(value=self._label_for(self.settings["src_lang"]))
        src_values = ["自动检测"] + [f"{c} · {n}" for c, n in _LANG_ROWS]
        self.src_combo = ttk.Combobox(src_box, textvariable=self.src_var,
                                      state="readonly", values=src_values,
                                      width=17, font=self.f_small,
                                      style="App.TCombobox")
        self.src_combo.pack()

        # 交换
        self.swap_lbl = tk.Label(self._picker, text="⇄", fg=THEME["fg_dim"],
                                 bg=THEME["bg"], font=(self.family, 14),
                                 cursor="hand2", padx=10)
        self.swap_lbl.pack(side=tk.LEFT, pady=(14, 0))
        self.swap_lbl.bind("<Enter>", lambda e: self.swap_lbl.config(
            fg=THEME["accent"], bg=THEME["surface2"]))
        self.swap_lbl.bind("<Leave>", lambda e: self.swap_lbl.config(
            fg=THEME["fg_dim"], bg=THEME["bg"]))
        self.swap_lbl.bind("<Button-1>", lambda e: self._swap_langs())
        self._tip(self.swap_lbl, "交换源语言与目标语言")

        tgt_box = tk.Frame(self._picker, bg=THEME["bg"])
        tgt_box.pack(side=tk.LEFT)
        tk.Label(tgt_box, text="目标语言", fg=THEME["fg_mute"], bg=THEME["bg"],
                 font=self.f_tiny).pack(anchor=tk.W)
        tgt_values = [f"{c} · {n}" for c, n in _LANG_ROWS]
        self.tgt_var = tk.StringVar(value=self._label_for(self.settings["tgt_lang"]))
        self.tgt_combo = ttk.Combobox(tgt_box, textvariable=self.tgt_var,
                                      state="readonly", values=tgt_values,
                                      width=17, font=self.f_small,
                                      style="App.TCombobox")
        self.tgt_combo.pack()

        self._header_stacked = None
        self.root.bind("<Configure>", self._on_root_configure)

    def _picker_needs_own_row(self) -> int:
        """返回"一行放不下"的宽度阈值。"""
        self.root.update_idletasks()
        need = (self._brand_row.winfo_reqwidth()
                + self._picker.winfo_reqwidth() + 40)
        return need

    def _on_root_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        need = self._picker_needs_own_row()
        stacked = event.width < need
        if stacked == self._header_stacked:
            return
        self._header_stacked = stacked
        self._picker.pack_forget()
        if stacked:
            # 语言选择单独占一行
            self._picker.pack(side=tk.TOP, anchor=tk.W, pady=(8, 0))
        else:
            self._picker.pack(side=tk.RIGHT)

    def _draw_mark(self, canvas: tk.Canvas) -> None:
        """在窗口里画一个和图标同款的小标记（渐变不好画，用同色系分层近似）。"""
        canvas.create_oval(2, 2, 32, 32, fill=THEME["accent_dk"], outline="")
        canvas.create_arc(2, 2, 32, 32, start=0, extent=180,
                          fill=THEME["accent"], outline="", style=tk.CHORD)
        canvas.create_text(17, 17, text="译", fill="#FFFFFF",
                           font=(self.family, 13, "bold"))

    def _subtitle(self) -> str:
        if not HAS_ENGINE:
            return "引擎不可用"
        where = "便携" if IS_PORTABLE else "LOCALAPPDATA"
        return f"离线 · {where} · Argos Translate"

    # ---- 输入卡片 ----
    def _build_input_card(self, parent) -> None:
        card = self._card(parent, side=tk.LEFT, padx=(0, 7))
        head = tk.Frame(card, bg=THEME["surface"])
        head.pack(fill=tk.X, padx=12, pady=(10, 0))
        tk.Label(head, text="原文", fg=THEME["fg_dim"], bg=THEME["surface"],
                 font=self.f_small).pack(side=tk.LEFT)
        self.src_hint = tk.Label(head, text="", fg=THEME["fg_mute"],
                                 bg=THEME["surface"], font=self.f_tiny)
        self.src_hint.pack(side=tk.RIGHT)

        wrap = tk.Frame(card, bg=THEME["surface"])
        wrap.pack(fill=tk.BOTH, expand=True, padx=12, pady=(6, 12))
        self.input_text = tk.Text(
            wrap, bg=THEME["surface2"], fg=THEME["fg"], font=self.f_body,
            # width/height 必须显式给：Tk 的 Text 默认 80x24 字符，
            # 那会让每张卡片的最小宽度变成 ~1100px，双栏布局撑爆窗口。
            width=1, height=1,
            insertbackground=THEME["accent"], relief=tk.FLAT, bd=0,
            padx=12, pady=10, wrap=tk.WORD, undo=True,
            selectbackground=THEME["accent_dk"], selectforeground="#FFFFFF",
            highlightthickness=1, highlightbackground=THEME["border"],
            highlightcolor=THEME["accent"], spacing1=1, spacing3=2)
        self.input_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(wrap, command=self.input_text.yview, style="Vertical.TScrollbar")
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(2, 0))
        self.input_text.config(yscrollcommand=sb.set)

        self._placeholder = ("在此输入或粘贴文本…\n\n"
                            "Ctrl+Enter 翻译　　Ctrl+Shift+T 读剪贴板并翻译")
        self._set_placeholder()
        self.input_text.bind("<FocusIn>", self._on_focus_in)
        self.input_text.bind("<FocusOut>", self._on_focus_out)
        self.input_text.bind("<<Modified>>", self._on_modified)

    # ---- 输出卡片 ----
    def _build_output_card(self, parent) -> None:
        card = self._card(parent, side=tk.RIGHT, padx=(7, 0))
        head = tk.Frame(card, bg=THEME["surface"])
        head.pack(fill=tk.X, padx=12, pady=(10, 0))
        tk.Label(head, text="译文", fg=THEME["fg_dim"], bg=THEME["surface"],
                 font=self.f_small).pack(side=tk.LEFT)
        self.result_hint = tk.Label(head, text="", fg=THEME["fg_mute"],
                                    bg=THEME["surface"], font=self.f_tiny)
        self.result_hint.pack(side=tk.RIGHT)
        self._hover_label(head, "复制", self._copy_result, pad=(8, 2),
                          tip="复制译文到剪贴板").pack(side=tk.RIGHT, padx=(0, 6))

        wrap = tk.Frame(card, bg=THEME["surface"])
        wrap.pack(fill=tk.BOTH, expand=True, padx=12, pady=(6, 12))
        self.output_text = tk.Text(
            wrap, bg=THEME["surface2"], fg=THEME["result"], font=self.f_body,
            width=1, height=1,
            insertbackground=THEME["accent"], relief=tk.FLAT, bd=0,
            padx=12, pady=10, wrap=tk.WORD, state=tk.DISABLED,
            selectbackground=THEME["accent_dk"], selectforeground="#FFFFFF",
            highlightthickness=1, highlightbackground=THEME["border"],
            highlightcolor=THEME["accent"], spacing1=1, spacing3=2)
        self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(wrap, command=self.output_text.yview, style="Vertical.TScrollbar")
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(2, 0))
        self.output_text.config(yscrollcommand=sb.set)

    # ---- 底栏 ----
    def _build_footer(self) -> None:
        footer = tk.Frame(self.root, bg=THEME["bg"])
        footer.pack(fill=tk.X, padx=16, pady=(10, 12))

        self.status_lbl = tk.Label(footer, text="就绪", fg=THEME["fg_dim"],
                                   bg=THEME["bg"], font=self.f_small)
        self.status_lbl.pack(side=tk.LEFT)

        self._hover_label(footer, "管理模型", self._open_model_manager,
                          fg=THEME["fg_dim"], tip="查看/下载翻译模型")\
            .pack(side=tk.LEFT, padx=(16, 0))
        self._hover_label(footer, "缓存管理", self._open_cache_manager,
                          fg=THEME["fg_dim"], tip="查看磁盘占用并清理可再生缓存")\
            .pack(side=tk.LEFT, padx=(4, 0))
        self._hover_label(footer, "历史", self._open_history,
                          fg=THEME["fg_dim"], tip="查看/恢复翻译历史")\
            .pack(side=tk.LEFT, padx=(4, 0))
        self._hover_label(footer, "模型目录",
                          lambda: self._open_folder(PACKAGES_DIR),
                          fg=THEME["fg_dim"], tip=PACKAGES_DIR)\
            .pack(side=tk.LEFT, padx=(4, 0))

        self.counter = tk.Label(footer, text="", fg=THEME["fg_mute"],
                                bg=THEME["bg"], font=self.f_tiny)
        self.counter.pack(side=tk.RIGHT)

        self._hover_label(footer, "清空", self._clear, fg=THEME["fg_dim"],
                          pad=(8, 2), tip="Esc").pack(side=tk.RIGHT, padx=(0, 10))

        self.topmost_var = tk.BooleanVar(value=bool(self.settings.get("topmost", True)))
        pin = tk.Checkbutton(footer, text="置顶", variable=self.topmost_var,
                             command=self._toggle_topmost, bg=THEME["bg"],
                             fg=THEME["fg_dim"], selectcolor=THEME["surface2"],
                             activebackground=THEME["bg"],
                             activeforeground=THEME["fg"], bd=0,
                             highlightthickness=0, font=self.f_tiny,
                             cursor="hand2", padx=0)
        pin.pack(side=tk.RIGHT, padx=(0, 10))
        self._tip(pin, "窗口始终显示在最前面")

    def _bind_keys(self) -> None:
        self.root.bind("<Control-Return>", lambda e: (self._translate(), "break")[1])
        self.root.bind("<Control-Shift-T>",
                       lambda e: (self._paste_and_translate(), "break")[1])
        self.root.bind("<Control-Shift-C>", lambda e: (self._copy_result(), "break")[1])
        self.root.bind("<Escape>", lambda e: self._clear())
        self.root.bind("<F5>", lambda e: (self._translate(), "break")[1])

    # ---------------- 状态 ----------------
    def _set_status(self, text: str, color: str = None) -> None:
        self.status_lbl.config(text=text, fg=color or THEME["fg_dim"])

    def _env_hint(self) -> str:
        if not HAS_ENGINE:
            return f"⚠ 翻译引擎不可用：{ENGINE_ERROR}"
        return f"模型目录：{PACKAGES_DIR}"

    @staticmethod
    def _label_for(code: str) -> str:
        if code == "auto":
            return "自动检测"
        return f"{code} · {LANG_NAMES.get(code, code)}"

    def _code_from(self, var: tk.StringVar) -> str:
        raw = var.get()
        if raw == "自动检测":
            return "auto"
        return raw.split(" · ")[0].strip()

    # ---------------- 占位文字 ----------------
    def _set_placeholder(self) -> None:
        self.input_text.delete("1.0", tk.END)
        self.input_text.insert("1.0", self._placeholder)
        self.input_text.config(fg=THEME["fg_mute"])
        self._has_placeholder = True

    def _get_input(self) -> str:
        if self._has_placeholder:
            return ""
        return self.input_text.get("1.0", "end-1c")

    def _on_focus_in(self, _event=None) -> None:
        if self._has_placeholder:
            self.input_text.delete("1.0", tk.END)
            self.input_text.config(fg=THEME["fg"])
            self._has_placeholder = False

    def _on_focus_out(self, _event=None) -> None:
        if not self.input_text.get("1.0", "end-1c").strip():
            self._set_placeholder()

    # ---------------- 输入变化 ----------------
    def _on_modified(self, _event=None) -> None:
        self.input_text.edit_modified(False)
        text = self._get_input()
        if not text.strip():
            self.src_hint.config(text="")
            self.counter.config(text="")
            return
        self.counter.config(text=f"{len(text)} 字")
        self.src_hint.config(text="检测中…", fg=THEME["fg_mute"])
        # 防抖：langdetect 是纯 Python，长文本很慢，不能每敲一个字都跑
        if self._after_id:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self._detect_gen += 1
        gen = self._detect_gen
        self._after_id = self.root.after(250, lambda: self._run_detect(gen, text))

    def _run_detect(self, gen: int, text: str) -> None:
        if gen != self._detect_gen:
            return
        code = detect_language(text)
        if gen != self._detect_gen:
            return
        name = LANG_NAMES.get(code, code)
        src = self._code_from(self.src_var)
        if src == "auto":
            self.src_hint.config(text=f"识别为 {name}", fg=THEME["fg_dim"])
        elif src != code:
            self.src_hint.config(
                text=f"⚠ 与所选 {LANG_NAMES.get(src, src)} 不一致", fg=THEME["warn"])
        else:
            self.src_hint.config(text=name, fg=THEME["fg_mute"])

    def _toggle_topmost(self) -> None:
        on = bool(self.topmost_var.get())
        self.root.attributes("-topmost", on)
        self.settings["topmost"] = on
        save_settings(self.settings)

    @staticmethod
    def _open_folder(path: str) -> None:
        try:
            os.startfile(path)                      # noqa: S606 (Windows only)
        except Exception:
            log.exception("open folder failed: %s", path)

    # ---------------- 核心动作 ----------------
    def _translate(self) -> None:
        if self._busy:
            self._set_status("正在翻译中…", THEME["warn"])
            return
        text = self._get_input()
        if not text.strip():
            self._set_status("请输入文本", THEME["bad"])
            return

        src = self._code_from(self.src_var)
        if src == "auto":
            src = detect_language(text)
        tgt = self._code_from(self.tgt_var)
        if src == tgt:
            self._set_status("源语言与目标语言相同", THEME["bad"])
            return

        # 长文提醒：这个模型是 CPU 跑的，约 350 字符/秒，长篇要等一会儿
        n_chars = len(text)
        est = n_chars / 350.0
        if n_chars > 4000:
            self._set_status(f"长文本 {n_chars} 字，预计 {est:.0f} 秒…", THEME["warn"])

        self._busy = True
        self._cancel = threading.Event()
        self._started = time.time()
        # 原文要留到结果回来时写历史。只存引用，不复制（长文复制一次不划算）。
        # 并发性：_busy 保证同一时刻只有一个翻译在跑，所以不会被下一次覆盖。
        self._pending_source = text
        self._pending_src = src
        self.translate_btn.config(state=tk.DISABLED, text="翻译中…",
                                  bg=THEME["surface2"], fg=THEME["fg_mute"])
        self.cancel_btn.pack(side=tk.RIGHT, padx=(8, 0))
        self._set_status("翻译中…", THEME["warn"])
        self.settings["src_lang"] = self._code_from(self.src_var)
        self.settings["tgt_lang"] = tgt

        cancel = self._cancel

        def report(message: str) -> None:
            self.results.put(("progress", message))

        def work() -> None:
            started = time.time()
            try:
                result = ENGINE.translate(text, src, tgt, report, cancel)
                self.results.put(("ok", result, time.time() - started, src, tgt))
            except TranslationCancelled:
                self.results.put(("cancelled", None))
            except Exception as exc:
                log.exception("translate failed")
                self.results.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True, name="translate").start()

    def _cancel_translate(self) -> None:
        if self._busy and getattr(self, "_cancel", None):
            self._cancel.set()
            self._set_status("正在取消…", THEME["warn"])

    def _paste_and_translate(self) -> None:
        try:
            clip = self.root.clipboard_get()
        except tk.TclError:
            self._set_status("剪贴板里没有文本", THEME["bad"])
            return
        if not clip.strip():
            self._set_status("剪贴板里没有文本", THEME["bad"])
            return
        self.input_text.delete("1.0", tk.END)
        self.input_text.insert("1.0", clip)
        self.input_text.config(fg=THEME["fg"])
        self._has_placeholder = False
        self._on_modified()
        self._translate()

    def _swap_langs(self) -> None:
        src = self._code_from(self.src_var)
        tgt = self._code_from(self.tgt_var)
        if src == "auto":
            self._set_status("源语言是自动检测，无法交换", THEME["warn"])
            return
        self.src_var.set(self._label_for(tgt))
        self.tgt_var.set(self._label_for(src))
        # 顺手把译文换到输入框，符合"反过来再翻回去"的直觉
        self.output_text.config(state=tk.NORMAL)
        out = self.output_text.get("1.0", "end-1c")
        self.output_text.config(state=tk.DISABLED)
        if out.strip():
            self.input_text.delete("1.0", tk.END)
            self.input_text.insert("1.0", out)
            self.input_text.config(fg=THEME["fg"])
            self._has_placeholder = False
            self._on_modified()
        self._set_status("已交换语言")

    def _clear(self) -> None:
        if self._busy:
            return
        self._set_placeholder()
        self.output_text.config(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.config(state=tk.DISABLED)
        self.counter.config(text="")
        self.src_hint.config(text="")
        self.result_hint.config(text="")
        self._set_status("就绪")

    def _copy_result(self) -> None:
        self.output_text.config(state=tk.NORMAL)
        text = self.output_text.get("1.0", "end-1c")
        self.output_text.config(state=tk.DISABLED)
        if not text.strip():
            self._set_status("还没有译文可复制", THEME["warn"])
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status("已复制到剪贴板", THEME["good"])

    # ---------------- 工作线程 -> 主线程 ----------------
    def _pump(self) -> None:
        try:
            while True:
                self._handle(self.results.get_nowait())
        except queue.Empty:
            pass
        self.root.after(60, self._pump)

    def _handle(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "progress":
            self._set_status(msg[1], THEME["warn"])
        elif kind == "ok":
            _, text, elapsed, src, tgt = msg
            self.output_text.config(state=tk.NORMAL)
            self.output_text.delete("1.0", tk.END)
            self.output_text.insert("1.0", text)
            self.output_text.config(state=tk.DISABLED)
            note = ""
            if text.count("\n") and len(text) > 2000:
                note = "　(分段翻译)"
            self.result_hint.config(
                text=f"{LANG_NAMES.get(src, src)} → {LANG_NAMES.get(tgt, tgt)}"
                     f"　{len(text)} 字　{elapsed:.1f}s{note}",
                fg=THEME["fg_mute"])
            self._set_status("翻译完成", THEME["good"])
            # 先记历史再解 busy：反过来的话按钮已经可点，用户可能在历史写完
            # 之前又发起一次翻译，_pending_source 就被覆盖了。
            self._record_history(src, tgt, text, elapsed)
            self._finish_busy()
        elif kind == "cancelled":
            self._set_status("已取消", THEME["warn"])
            self.result_hint.config(text="已取消", fg=THEME["warn"])
            self._finish_busy()
        elif kind == "error":
            self._set_status("翻译失败", THEME["bad"])
            self.output_text.config(state=tk.NORMAL)
            self.output_text.delete("1.0", tk.END)
            self.output_text.insert("1.0", f"翻译失败\n\n{msg[1]}")
            self.output_text.config(state=tk.DISABLED)
            self._finish_busy()
            messagebox.showerror("翻译失败", msg[1])
        elif kind == "warm":
            self._set_status(msg[1])

    def _finish_busy(self) -> None:
        self._busy = False
        self.cancel_btn.pack_forget()
        self.translate_btn.config(state=tk.NORMAL, text="译  Translate",
                                  bg=THEME["accent"], fg="#FFFFFF")
        save_settings(self.settings)

    def _record_history(self, src: str, tgt: str, result: str,
                        elapsed: float) -> None:
        """把这次翻译写进历史。

        这里**必须**吞掉一切异常。调用点在"翻译已经成功"之后：如果记录失败
        把异常抛出来，用户看到的是"翻译失败"，而翻译其实成功了 —— 拿一个
        附加功能去毁掉主功能，是最糟的失败方式。

        （HistoryStore.record 本身已保证不抛；这里再包一层是防御性的，
        因为将来可能有人改动它，或者 HISTORY 单例被换掉。）
        """
        try:
            source = getattr(self, "_pending_source", "") or ""
            HISTORY.record(src, tgt, source, result, elapsed)
        except Exception:
            log.exception("history record failed")     # 只记日志，不影响界面
        finally:
            self._pending_source = ""

    def _start_warmup(self) -> None:
        """后台把已安装模型扫进内存，让第一次翻译不用现扫。"""
        if not HAS_ENGINE:
            self._set_status("引擎未安装", THEME["bad"])
            return

        def work() -> None:
            try:
                count = ENGINE.installed_count()
                if count:
                    self.results.put(("warm", f"就绪 · {count} 个模型"))
                else:
                    self.results.put(("warm", "就绪 · 尚未安装模型"))
            except Exception:
                log.exception("warmup failed")

        threading.Thread(target=work, daemon=True, name="warmup").start()

    # ---------------- 模型管理 ----------------
    def _open_model_manager(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("模型管理")
        dlg.configure(bg=THEME["bg"])
        dlg.geometry("780x620")
        dlg.minsize(620, 460)
        dlg.transient(self.root)
        dlg.attributes("-topmost", bool(self.topmost_var.get()))
        ico = _icon_path()
        if ico:
            try:
                dlg.iconbitmap(default=ico)
            except tk.TclError:
                pass

        head = tk.Frame(dlg, bg=THEME["bg"])
        head.pack(fill=tk.X, padx=16, pady=(14, 0))
        self._dlg_count = tk.Label(head, text="", fg=THEME["fg"], bg=THEME["bg"],
                                   font=(self.family, 11, "bold"))
        self._dlg_count.pack(side=tk.LEFT)
        tk.Label(head, text=PACKAGES_DIR, fg=THEME["fg_mute"], bg=THEME["bg"],
                 font=self.f_tiny).pack(side=tk.RIGHT)

        tk.Label(dlg,
                 text="任意语言对都能翻：只要「源→英」与「英→目标」两个模型齐全，会自动经英文中转。",
                 fg=THEME["fg_mute"], bg=THEME["bg"], font=self.f_tiny,
                 anchor=tk.W).pack(fill=tk.X, padx=16, pady=(2, 8))

        # 已安装列表
        tree_wrap = self._card(dlg)
        tree_wrap.master.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 10))
        cols = ("src", "tgt", "names")
        tree = ttk.Treeview(tree_wrap, columns=cols, show="headings",
                            style="Card.Treeview", selectmode="browse")
        tree.heading("src", text="源")
        tree.heading("tgt", text="目标")
        tree.heading("names", text="语言")
        tree.column("src", width=70, anchor=tk.CENTER, stretch=False)
        tree.column("tgt", width=70, anchor=tk.CENTER, stretch=False)
        tree.column("names", width=440, anchor=tk.W)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(1, 0), pady=1)
        tsb = ttk.Scrollbar(tree_wrap, command=tree.yview, style="Vertical.TScrollbar")
        tsb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 1), pady=1)
        tree.config(yscrollcommand=tsb.set)

        def refresh_list() -> None:
            tree.delete(*tree.get_children())
            pairs = sorted(ENGINE.installed_pairs())
            self._dlg_count.config(text=f"已安装 {len(pairs)} 个模型")
            if not pairs:
                tree.insert("", tk.END, values=("", "", "（还没有模型，用下面的下载区装几个）"))
                return
            for a, b in pairs:
                tree.insert("", tk.END, values=(
                    a, b, f"{LANG_NAMES.get(a, a)} → {LANG_NAMES.get(b, b)}"))

        refresh_list()

        # 下载区
        box = self._card(dlg, side=None, fill=tk.X)
        box.master.pack(fill=tk.X, padx=16, pady=(0, 8))
        inner = tk.Frame(box, bg=THEME["surface"])
        inner.pack(fill=tk.X, padx=14, pady=12)
        tk.Label(inner, text="下载 / 安装新模型", fg=THEME["fg_dim"],
                 bg=THEME["surface"], font=self.f_small).pack(anchor=tk.W)

        row = tk.Frame(inner, bg=THEME["surface"])
        row.pack(fill=tk.X, pady=(8, 0))
        from_var = tk.StringVar(value="en")
        to_var = tk.StringVar(value="zh")
        ttk.Combobox(row, textvariable=from_var, state="readonly", width=7,
                     values=LANG_CODES, font=self.f_small,
                     style="App.TCombobox").pack(side=tk.LEFT)
        tk.Label(row, text="→", fg=THEME["fg_mute"], bg=THEME["surface"],
                 font=self.f_small).pack(side=tk.LEFT, padx=8)
        ttk.Combobox(row, textvariable=to_var, state="readonly", width=7,
                     values=LANG_CODES, font=self.f_small,
                     style="App.TCombobox").pack(side=tk.LEFT)

        dl_state = tk.Label(inner, text="", fg=THEME["warn"], bg=THEME["surface"],
                            font=self.f_tiny, anchor=tk.W, justify=tk.LEFT,
                            wraplength=680)
        dl_state.pack(fill=tk.X, pady=(8, 0))

        def ui(fn, *a, **kw):
            dlg.after(0, lambda: fn(*a, **kw))

        def do_refresh_index() -> None:
            def work() -> None:
                ui(dl_state.config, text="正在获取可下载清单…", fg=THEME["warn"])
                try:
                    n = ENGINE.refresh_available()
                    ui(dl_state.config,
                       text=f"清单已更新，共 {n} 个包可下载。", fg=THEME["good"])
                except Exception as exc:
                    log.exception("refresh index failed")
                    ui(dl_state.config, text=f"获取失败：{exc}", fg=THEME["bad"])
            threading.Thread(target=work, daemon=True).start()

        def do_download() -> None:
            a, b = from_var.get(), to_var.get()
            if a == b:
                dl_state.config(text="源语言和目标语言不能相同", fg=THEME["bad"])
                return
            if (a, b) in ENGINE.installed_pairs():
                dl_state.config(text=f"{a} → {b} 已经装好了", fg=THEME["good"])
                return

            def work() -> None:
                try:
                    ENGINE.install_pair(
                        a, b, lambda m: ui(dl_state.config, text=m,
                                           fg=THEME["warn"]))
                    ui(dl_state.config, text=f"{a} → {b} 安装完成",
                       fg=THEME["good"])
                    ui(refresh_list)
                except Exception as exc:
                    log.exception("install failed")
                    ui(dl_state.config, text=f"安装失败：{exc}", fg=THEME["bad"])
            threading.Thread(target=work, daemon=True).start()

        btns = tk.Frame(inner, bg=THEME["surface"])
        btns.pack(fill=tk.X, pady=(10, 0))
        self._button(btns, "刷新可下载清单", do_refresh_index).pack(side=tk.LEFT)
        self._button(btns, "下载并安装", do_download, kind="primary")\
            .pack(side=tk.LEFT, padx=(8, 0))
        self._button(btns, "打开模型目录",
                     lambda: self._open_folder(PACKAGES_DIR)).pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(dlg,
                 text="下载需要联网。离线环境下可把别处的 models/packages 目录整个复制过来，"
                      "或用目录链接指过去（mklink /J）。",
                 fg=THEME["fg_mute"], bg=THEME["bg"], font=self.f_tiny,
                 anchor=tk.W, justify=tk.LEFT).pack(fill=tk.X, padx=16, pady=(0, 12))
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    # ---------------- 缓存管理 ----------------
    def _open_cache_manager(self) -> None:
        """磁盘占用一览 + 清理可再生缓存。

        两个刻意的设计：
          * 模型（models）只报告、不提供删除。它占 99% 的空间但属于用户资产，
            删掉要重新下几百 MB。清空按钮在接口层就够不到它（CacheManager
            只接受 disposable 的名字），所以界面不需要靠自觉来防误删。
          * 目录遍历实测约 350 ms（20 GB 量级），放到后台线程算，
            开窗不卡。
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("缓存管理")
        dlg.configure(bg=THEME["bg"])
        dlg.geometry("760x560")
        dlg.minsize(620, 420)
        dlg.transient(self.root)
        dlg.attributes("-topmost", bool(self.topmost_var.get()))
        ico = _icon_path()
        if ico:
            try:
                dlg.iconbitmap(default=ico)
            except tk.TclError:
                pass

        head = tk.Frame(dlg, bg=THEME["bg"])
        head.pack(fill=tk.X, padx=16, pady=(14, 0))
        tk.Label(head, text="磁盘占用", fg=THEME["fg"], bg=THEME["bg"],
                 font=(self.family, 11, "bold")).pack(side=tk.LEFT)
        self._cache_total = tk.Label(head, text="统计中…", fg=THEME["fg_mute"],
                                     bg=THEME["bg"], font=self.f_tiny)
        self._cache_total.pack(side=tk.RIGHT)

        tk.Label(dlg,
                 text="翻译模型是你的资产，不会在这里被删除。「可再生」的项目删掉后会在"
                      "下次用到时自动重建；「用户记录」（翻译历史）删了不可恢复，"
                      "所以单独一个按钮、单独确认。",
                 fg=THEME["fg_mute"], bg=THEME["bg"], font=self.f_tiny,
                 anchor=tk.W, justify=tk.LEFT, wraplength=700)\
            .pack(fill=tk.X, padx=16, pady=(2, 8))

        holder = self._card(dlg)
        holder.master.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 10))
        cols = ("name", "size", "kind")
        tree = ttk.Treeview(holder, columns=cols, show="headings",
                            style="Card.Treeview", selectmode="browse")
        tree.heading("name", text="项目")
        tree.heading("size", text="占用")
        tree.heading("kind", text="保留等级")
        tree.column("name", width=210, anchor=tk.W, stretch=False)
        tree.column("size", width=110, anchor=tk.E, stretch=False)
        tree.column("kind", width=380, anchor=tk.W)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(1, 0), pady=1)
        tsb = ttk.Scrollbar(holder, command=tree.yview, style="Vertical.TScrollbar")
        tsb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 1), pady=1)
        tree.config(yscrollcommand=tsb.set)

        state = tk.Label(dlg, text="", fg=THEME["fg_mute"], bg=THEME["bg"],
                         font=self.f_tiny, anchor=tk.W, justify=tk.LEFT,
                         wraplength=700)
        state.pack(fill=tk.X, padx=16, pady=(0, 6))

        cache_q: queue.Queue = queue.Queue()
        alive = {"yes": True}

        def ui(fn, *a, **kw):
            """把界面更新排进队列，由主线程的轮询消费。

            不能在后台线程里直接调 dlg.after() —— Tk 不是线程安全的
            （主窗口用的是同一套 queue + 轮询的做法）。对话框自己一个队列，
            关窗时用 stop 标志让轮询退出。
            """
            cache_q.put((fn, a, kw))

        def pump() -> None:
            try:
                while True:
                    fn, a, kw = cache_q.get_nowait()
                    fn(*a, **kw)
            except queue.Empty:
                pass
            except tk.TclError:
                return                        # 窗口已销毁
            # 同历史对话框：窗口没了还调 after() 会在 Tcl 层报后台错误
            if alive["yes"] and dlg.winfo_exists():
                dlg.after(60, pump)

        def on_close() -> None:
            alive["yes"] = False
            dlg.destroy()

        def render(items) -> None:
            tree.delete(*tree.get_children())
            total = 0
            for it in items:
                total += it.size
                # 等级标签直接来自模块，界面不自己判断能不能删
                tree.insert("", tk.END, values=(
                    f"{it.label}  ({it.name})", _fmt_size(it.size),
                    it.retention_label))
                tree.insert("", tk.END, values=("    " + it.purpose, "", ""))
            self._cache_total.config(text=f"合计 {_fmt_size(total)}")

        def load(force: bool = False) -> None:
            def work() -> None:
                try:
                    items = CACHE.inventory(force=force)
                except Exception as exc:                     # pragma: no cover
                    log.exception("cache inventory failed")
                    ui(state.config, text=f"统计失败：{exc}", fg=THEME["bad"])
                    return
                ui(render, items)
                dl = sum(i.size for i in items if i.disposable)
                rec = sum(i.size for i in items
                          if i.retention == Retention.USER_RECORD)
                ui(state.config,
                   text=f"可再生 {_fmt_size(dl)}　·　用户记录 {_fmt_size(rec)}"
                        f"　·　翻译模型不计入清理",
                   fg=THEME["fg_mute"])
            threading.Thread(target=work, daemon=True, name="cache-scan").start()

        def do_clear() -> None:
            if self._busy:
                state.config(text="正在翻译中，先等它结束再清理", fg=THEME["warn"])
                return
            dl = CACHE.disposable_size()
            if dl == 0:
                state.config(text="可清理的项目已经是空的", fg=THEME["good"])
                return
            if not messagebox.askyesno(
                    "确认清理",
                    f"将清理约 {_fmt_size(dl)} 的可再生缓存：\n\n"
                    "· 分句模型（下次翻译时会重新下载或从随包副本恢复）\n"
                    "· 模型清单索引（下次刷新清单时重建）\n"
                    "· 运行日志\n\n"
                    "翻译模型和翻译历史都不会被删除。继续吗？",
                    parent=dlg):
                return

            def work() -> None:
                ui(state.config, text="正在清理…", fg=THEME["warn"])
                try:
                    rep = CACHE.clear()          # 只删 DISPOSABLE
                except Exception as exc:
                    log.exception("cache clear failed")
                    ui(state.config, text=f"清理失败：{exc}", fg=THEME["bad"])
                    return
                ui(state.config, text=rep.summary(), fg=THEME["good"])
                ui(load, True)
            threading.Thread(target=work, daemon=True, name="cache-clear").start()

        def do_purge_history() -> None:
            """删除用户记录。与清理缓存分开：这个不可恢复，措辞和确认都更重。"""
            if self._busy:
                state.config(text="正在翻译中，先等它结束", fg=THEME["warn"])
                return
            try:
                st = HISTORY.summary()
            except Exception as exc:
                state.config(text=f"读取历史失败：{exc}", fg=THEME["bad"])
                return
            if st.count == 0:
                state.config(text="历史已经是空的", fg=THEME["good"])
                return
            if not messagebox.askyesno(
                    "确认删除历史",
                    f"将删除全部 {st.count} 条翻译历史（{_fmt_size(st.bytes)}）。\n\n"
                    "这个操作**不可恢复**。翻译模型和缓存不受影响。\n\n继续吗？",
                    parent=dlg, icon="warning", default="no"):
                return

            def work() -> None:
                ui(state.config, text="正在删除历史…", fg=THEME["warn"])
                try:
                    rep = CACHE.purge(["history"])
                except Exception as exc:
                    log.exception("history purge failed")
                    ui(state.config, text=f"删除失败：{exc}", fg=THEME["bad"])
                    return
                ui(state.config, text=f"历史已删除（{rep.summary()}）",
                   fg=THEME["good"])
                ui(load, True)
            threading.Thread(target=work, daemon=True, name="hist-purge").start()

        btns = tk.Frame(dlg, bg=THEME["bg"])
        btns.pack(fill=tk.X, padx=16, pady=(0, 14))
        self._button(btns, "重新统计", lambda: load(True)).pack(side=tk.LEFT)
        self._button(btns, "清理可再生缓存", do_clear).pack(side=tk.LEFT, padx=(8, 0))
        self._button(btns, "删除翻译历史", do_purge_history)\
            .pack(side=tk.LEFT, padx=(8, 0))
        self._button(btns, "打开程序目录",
                     lambda: self._open_folder(DATA_ROOT)).pack(side=tk.LEFT, padx=(8, 0))

        load()
        dlg.after(60, pump)
        dlg.protocol("WM_DELETE_WINDOW", on_close)
        dlg.bind("<Escape>", lambda e: on_close())

    # ---------------- 翻译历史 ----------------
    PAGE = 50                                # 一页多少条

    def _open_history(self) -> None:
        """历史窗口：分页浏览、恢复到输入框、清空。

        分页而不是一次全塞：条数上限是 200，但每条内容不截断，长文条的文本
        很大，一次性填进 Treeview 会卡。
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("翻译历史")
        dlg.configure(bg=THEME["bg"])
        dlg.geometry("860x620")
        dlg.minsize(680, 460)
        dlg.transient(self.root)
        dlg.attributes("-topmost", bool(self.topmost_var.get()))
        ico = _icon_path()
        if ico:
            try:
                dlg.iconbitmap(default=ico)
            except tk.TclError:
                pass

        head = tk.Frame(dlg, bg=THEME["bg"])
        head.pack(fill=tk.X, padx=16, pady=(14, 0))
        tk.Label(head, text="翻译历史", fg=THEME["fg"], bg=THEME["bg"],
                 font=(self.family, 11, "bold")).pack(side=tk.LEFT)
        self._hist_info = tk.Label(head, text="", fg=THEME["fg_mute"],
                                   bg=THEME["bg"], font=self.f_tiny)
        self._hist_info.pack(side=tk.RIGHT)

        tk.Label(dlg,
                 text="双击一条可把原文放回输入框；也可以只恢复译文。"
                      "历史只存在本机，不会上传。",
                 fg=THEME["fg_mute"], bg=THEME["bg"], font=self.f_tiny,
                 anchor=tk.W, justify=tk.LEFT, wraplength=820)\
            .pack(fill=tk.X, padx=16, pady=(2, 8))

        holder = self._card(dlg)
        holder.master.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))
        cols = ("time", "pair", "size", "preview")
        tree = ttk.Treeview(holder, columns=cols, show="headings",
                            style="Card.Treeview", selectmode="browse")
        for cid, text, w, anchor in (("time", "时间", 130, tk.W),
                                     ("pair", "语言", 90, tk.CENTER),
                                     ("size", "字数", 90, tk.E),
                                     ("preview", "原文", 420, tk.W)):
            tree.heading(cid, text=text)
            tree.column(cid, width=w, anchor=anchor,
                        stretch=(cid == "preview"))
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(1, 0), pady=1)
        tsb = ttk.Scrollbar(holder, command=tree.yview, style="Vertical.TScrollbar")
        tsb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 1), pady=1)
        tree.config(yscrollcommand=tsb.set)

        detail = tk.Text(dlg, height=6, bg=THEME["surface2"], fg=THEME["result"],
                         font=self.f_small, relief=tk.FLAT, bd=0, wrap=tk.WORD,
                         padx=10, pady=8, highlightthickness=1,
                         highlightbackground=THEME["border"])
        detail.pack(fill=tk.X, padx=16, pady=(0, 8))
        detail.insert("1.0", "选中一条历史查看内容。")
        detail.config(state=tk.DISABLED)

        state = tk.Label(dlg, text="", fg=THEME["fg_mute"], bg=THEME["bg"],
                         font=self.f_tiny, anchor=tk.W)
        state.pack(fill=tk.X, padx=16, pady=(0, 6))

        page_no = {"n": 0}
        shown: list = []                    # 当前页的 HistoryEntry

        def render() -> None:
            tree.delete(*tree.get_children())
            stats = HISTORY.summary()
            total_pages = max(1, (stats.count + self.PAGE - 1) // self.PAGE)
            if page_no["n"] >= total_pages:
                page_no["n"] = max(0, total_pages - 1)
            shown.clear()
            shown.extend(HISTORY.page(page_no["n"] * self.PAGE, self.PAGE))
            for idx, e in enumerate(shown):
                when = time.strftime("%m-%d %H:%M",
                                     time.localtime(e.ts)) if e.ts else "—"
                a, b = e.chars
                tree.insert("", tk.END, iid=str(idx), values=(
                    when, f"{e.src}→{e.tgt}", f"{a}→{b}", e.head(64)))
            self._hist_info.config(
                text=f"{stats.count} 条 · {_fmt_size(stats.bytes)}")
            empty = "（还没有历史）" if stats.count == 0 else ""
            state.config(
                text=f"第 {page_no['n'] + 1}/{total_pages} 页　"
                     f"上限 {stats.max_entries} 条 / {_fmt_size(stats.max_bytes)}"
                     f"　{empty}")

        def on_select(_e=None) -> None:
            sel = tree.selection()
            if not sel:
                return
            i = int(sel[0])
            if i >= len(shown):
                return
            e = shown[i]
            detail.config(state=tk.NORMAL)
            detail.delete("1.0", tk.END)
            detail.insert("1.0",
                          f"【原文 {e.src}】\n{e.source}\n\n"
                          f"【译文 {e.tgt}】\n{e.target}")
            detail.config(state=tk.DISABLED)

        def put(text: str, label: str) -> None:
            self.input_text.delete("1.0", tk.END)
            self.input_text.insert("1.0", text)
            self.input_text.config(fg=THEME["fg"])
            self._has_placeholder = False
            self._on_modified()
            self._set_status(f"已恢复到输入框（{label}）", THEME["good"])

        def restore_source(_e=None) -> None:
            sel = tree.selection()
            if not sel or int(sel[0]) >= len(shown):
                state.config(text="先选中一条历史", fg=THEME["warn"])
                return
            put(shown[int(sel[0])].source, "原文")

        def restore_target() -> None:
            sel = tree.selection()
            if not sel or int(sel[0]) >= len(shown):
                state.config(text="先选中一条历史", fg=THEME["warn"])
                return
            put(shown[int(sel[0])].target, "译文")

        def goto(delta: int) -> None:
            page_no["n"] = max(0, page_no["n"] + delta)
            render()

        def do_clear() -> None:
            stats = HISTORY.summary()
            if stats.count == 0:
                state.config(text="历史已经是空的", fg=THEME["good"])
                return
            if not messagebox.askyesno(
                    "确认清空历史",
                    f"将删除全部 {stats.count} 条翻译历史（{_fmt_size(stats.bytes)}）。\n\n"
                    "这个操作**不可恢复**，原文和译文都会丢失。\n"
                    "翻译模型和缓存不受影响。\n\n继续吗？",
                    parent=dlg, icon="warning", default="no"):
                return

            def work() -> None:
                ui(state.config, text="正在清空…", fg=THEME["warn"])
                try:
                    rep = CACHE.purge(["history"])      # 走缓存管理，委托给 store
                except Exception as exc:
                    log.exception("history purge failed")
                    ui(state.config, text=f"清空失败：{exc}", fg=THEME["bad"])
                    return
                ui(state.config, text=f"已清空（{rep.summary()}）", fg=THEME["good"])
                ui(render)
            threading.Thread(target=work, daemon=True, name="hist-purge").start()

        hist_q: queue.Queue = queue.Queue()
        alive = {"yes": True}

        def ui(fn, *a, **kw):
            hist_q.put((fn, a, kw))

        def pump() -> None:
            try:
                while True:
                    fn, a, kw = hist_q.get_nowait()
                    fn(*a, **kw)
            except queue.Empty:
                pass
            except tk.TclError:
                return
            # 窗口可能已经被销毁；不先确认就 after() 会在解释器里报
            # "invalid command name ..._pump"。这不是异常，是 Tcl 的
            # 后台错误，只能靠事前检查避免。
            if alive["yes"] and dlg.winfo_exists():
                dlg.after(60, pump)

        def on_close() -> None:
            alive["yes"] = False
            dlg.destroy()

        tree.bind("<<TreeviewSelect>>", on_select)
        tree.bind("<Double-1>", restore_source)

        bar = tk.Frame(dlg, bg=THEME["bg"])
        bar.pack(fill=tk.X, padx=16, pady=(0, 14))
        self._button(bar, "上一页", lambda: goto(-1)).pack(side=tk.LEFT)
        self._button(bar, "下一页", lambda: goto(1)).pack(side=tk.LEFT, padx=(6, 0))
        self._button(bar, "刷新", render).pack(side=tk.LEFT, padx=(6, 0))
        self._button(bar, "清空历史", do_clear).pack(side=tk.LEFT, padx=(18, 0))
        self._button(bar, "恢复译文", restore_target).pack(side=tk.RIGHT)
        self._button(bar, "恢复原文", restore_source,
                     kind="primary").pack(side=tk.RIGHT, padx=(0, 6))

        render()
        dlg.after(60, pump)
        dlg.protocol("WM_DELETE_WINDOW", on_close)
        dlg.bind("<Escape>", lambda e: on_close())

    # ---------------- 生命周期 ----------------
    def _log_geometry(self) -> None:
        self.root.update_idletasks()
        try:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            x, y = self.root.winfo_x(), self.root.winfo_y()
            log.info("window: %dx%d+%d+%d  screen=%dx%d  client=%dx%d", w, h, x, y,
                     sw, sh, self.root.winfo_reqwidth(),
                     self.root.winfo_reqheight())
        except tk.TclError:
            pass

    def _on_close(self) -> None:
        try:
            self.settings["geometry"] = self.root.winfo_geometry()
            save_settings(self.settings)
        finally:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


# 必须在 Tk 建窗口之前声明 DPI 感知，否则高分屏下整窗被系统拉伸、字发糊。
_DPI_AWARE_OK = _enable_dpi_awareness()


# ======================================================================
# 10. 入口
# ======================================================================

def _selftest() -> int:
    """`--selftest`：不开窗口，直接检查打包后的路径/引擎/翻译是否正常。

    打包成 windowed exe 后没有控制台，所以结果同时写到 stdout 和日志文件。

    设计约束：**自检绝不能触发联网下载**。没有本地模型时只报告缺模型并跳过
    翻译检查（否则在一个干净的目录里会卡十几分钟下载语言模型，
    看起来就像程序死机了）。

    注：PACKAGES_DIR 在模块顶部就通过 ARGOS_PACKAGES_DIR 固定成程序自己的目录了，
    外部环境变量影响不到（这是刻意的：数据一律跟着程序走）。
    """
    lines: list[str] = []

    def say(text: str) -> None:
        lines.append(text)
        try:
            print(text, flush=True)
        except Exception:
            pass

    problems: list[str] = []
    say("=== 桌面翻译助手 self-test ===")
    say(f"frozen        : {bool(getattr(sys, 'frozen', False))}")
    say(f"APP_DIR       : {APP_DIR}")
    say(f"DATA_ROOT     : {DATA_ROOT}")
    say(f"IS_PORTABLE   : {IS_PORTABLE}")
    try:
        DATA_ROOT.encode("ascii")
        say("path is ASCII : True")
    except UnicodeEncodeError:
        say("path is ASCII : False")
        problems.append("DATA_ROOT is not ASCII")
    say(f"CACHE_DIR     : {CACHE_DIR}")
    say(f"PACKAGES_DIR  : {PACKAGES_DIR}")
    say(f"engine        : {HAS_ENGINE}  (err={ENGINE_ERROR or 'none'})")
    say(f"minisbd       : {HAS_MINISBD}  ({MINISBD_DIR})")
    say(f"langdetect    : {HAS_LANGDETECT}")

    for name, ok in (("engine", HAS_ENGINE), ("minisbd", HAS_MINISBD),
                     ("langdetect", HAS_LANGDETECT)):
        if not ok:
            problems.append(f"{name} unavailable")

    t0 = time.time()
    count = ENGINE.installed_count()
    say(f"installed     : {count} models  ({time.time()-t0:.2f}s)")
    if count == 0:
        # 排查用：把"引擎实际在看哪个目录"和"扫描本身的异常"都摊开。
        # 光看 "0 models" 无法区分"目录里真没有"和"扫描路径不对/扫描抛异常"。
        try:
            import argostranslate.settings as _S
            say(f"  argos 包目录 : {_S.package_data_dir}")
        except Exception as exc:
            say(f"  argos 包目录 : 读取失败 {type(exc).__name__}: {exc}")
        try:
            _dirs = sorted(os.listdir(PACKAGES_DIR))
            say(f"  目录项       : {len(_dirs)} 个"
                + (f"（前 3：{_dirs[:3]}）" if _dirs else "（目录是空的）"))
        except Exception as exc:
            say(f"  目录项       : 读取失败 {type(exc).__name__}: {exc}")
        try:
            import argostranslate.package as _P
            say(f"  扫描结果     : {len(_P.get_installed_packages())} 个包")
        except Exception as exc:
            say(f"  扫描异常     : {type(exc).__name__}: {exc}")

    import minisbd.models as _mm
    onnx = [f for f in os.listdir(os.path.join(CACHE_DIR, "minisbd"))
            if f.endswith(".onnx")] if os.path.isdir(MINISBD_DIR) else []
    say(f"sbd models    : {len(onnx)} onnx files")
    if not onnx:
        problems.append("no MiniSBD sentence-splitter models present")

    # 磁盘占用一览。这里顺便能暴露"同一份缓存存在两份"这类布局问题：
    # 打包后 CACHE_DIR 在 _internal 下，而程序目录里可能还留着一份旧的。
    say("")
    say("--- cache ---")
    try:
        for item in CACHE.inventory():
            say(f"  {item.retention:<12} {item.label:<12} "
                f"{_fmt_size(item.size):>9}  {item.path}")
        say(f"  合计占用 {_fmt_size(CACHE.total_size())}，"
            f"可再生 {_fmt_size(CACHE.disposable_size())}")
    except Exception as exc:
        say(f"  统计失败：{type(exc).__name__}: {exc}")
        problems.append("cache inventory failed")

    say("")
    say("--- history ---")
    try:
        st = HISTORY.summary()
        say(f"  条目      : {st.count} / {st.max_entries}")
        say(f"  占用      : {_fmt_size(st.bytes)} / {_fmt_size(st.max_bytes)}")
        say(f"  文件      : {st.path}")
        # 写入一次再读回来，确认这条链路是通的（自检不该只读）
        probe = HISTORY.record("en", "zh", "selftest probe", "自检探针", 0.0)
        if not probe:
            say(f"  写入探针  : 失败（{probe.reason}）")
            problems.append(f"history write failed: {probe.reason}")
        else:
            back = HISTORY.page(0, 1)
            ok = bool(back) and back[0].source == "selftest probe"
            say(f"  写入并读回: {'ok' if ok else '不一致'}")
            if not ok:
                problems.append("history round-trip mismatch")
    except Exception as exc:
        say(f"  检查失败：{type(exc).__name__}: {exc}")
        problems.append("history check failed")

    say("")
    say("--- detection ---")
    for text, want in (("Hello world, this is a test.", "en"),
                       ("你好，世界！这是测试。", "zh"),
                       ("これはテストです。", "ja"),
                       ("안녕하세요", "ko")):
        got = detect_language(text)
        flag = "ok " if got == want else "BAD"
        say(f"  {flag} {text[:24]:<26} -> {got} (want {want})")
        if got != want:
            problems.append(f"detect {text[:12]!r} gave {got}")

    # 没有模型时到此为止。
    # 之前这里会继续往下走，触发"缺模型 → 联网下载"，在一个干净的目录里会
    # 卡住十几分钟（下载 80-160 MB/语言对），自检看起来就像死机了。
    # 自检的职责是验证"程序本身是否正常"，不是去装模型。
    if count == 0:
        say("")
        say("--- translation ---")
        say("  SKIPPED: 本地没有语言模型")
        say(f"  模型目录: {PACKAGES_DIR}")
        say("  装入方式（任选其一）：")
        say("    1) 把语言模型包解压到 models/packages/ 下")
        say("    2) 启动程序 → 底栏「管理模型」→ 刷新清单 → 下载并安装")
        say("")
        say("SELFTEST PASSED (未安装模型，已跳过翻译检查)")
        log.info("selftest finished without models; translation checks skipped")
        try:
            with open(os.path.join(LOG_DIR, "selftest.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except OSError:
            pass
        return 1 if problems else 0

    say("")
    say("--- translation (offline, using local models) ---")
    cases = [
        ("en", "zh", "Good morning! How are you today?"),
        ("zh", "en", "你好，世界！这是离线翻译。"),
        ("ja", "zh", "これはテストです。"),
        ("en", "ja", "This is a test. Thank you."),
    ]
    for src, tgt, text in cases:
        t = time.time()
        try:
            out = ENGINE.translate(text, src, tgt)
            ok = bool(out.strip()) and out.strip() != text.strip()
            say(f"  {'ok ' if ok else 'BAD'} {src}->{tgt} ({time.time()-t:.1f}s): {out}")
            if not ok:
                problems.append(f"translate {src}->{tgt} produced no output")
        except Exception as exc:
            say(f"  BAD {src}->{tgt}: {type(exc).__name__}: {exc}")
            problems.append(f"translate {src}->{tgt} failed")

    say("")
    say("--- long text (chunked, must not lose content) ---")
    prose = [
        "The old lighthouse had stood on the cliff for one hundred and twenty years.",
        "Every evening its beam swept across the water like a slow, patient hand.",
        "Sailors claimed they could see it from thirty miles out on a clear night.",
        "When the storm arrived in October, the keeper refused to leave his post.",
        "Waves broke over the rocks below with a sound like distant thunder.",
        "He wrote three letters that night, though he never posted any of them.",
        "By morning the wind had dropped and the sea was almost flat and grey.",
        "A fishing boat appeared at noon, drifting without lights or crew.",
        "The keeper rowed out to it and found nothing but a broken compass.",
        "He kept that compass on his windowsill for the rest of his life.",
    ]
    long_text = " ".join(prose * 4)
    chunks = split_for_translation(long_text, Engine.CHUNK_CHARS)
    rebuilt = _re.sub(r"\s+", "", "".join(chunks))
    original = _re.sub(r"\s+", "", long_text)
    say(f"  {len(long_text)} chars -> {len(chunks)} chunks "
        f"{[len(c) for c in chunks]}")
    say(f"  chunking is lossless: {rebuilt == original}")
    if rebuilt != original:
        problems.append("chunking lost or duplicated content")

    seen: list[str] = []
    t0 = time.time()
    try:
        out = ENGINE.translate(long_text, "en", "zh",
                               lambda m: seen.append(m))
        elapsed = time.time() - t0
        say(f"  translated in {elapsed:.1f}s "
            f"({len(long_text)/max(elapsed, 0.01):.0f} chars/s)")
        say(f"  progress callbacks: {len(seen)} ({seen[:2]})")
        say(f"  degenerate output: {is_degenerate(out)}")
        if not seen:
            problems.append("no progress callbacks for long text")
        if is_degenerate(out):
            problems.append("long-text output degenerated")
        themes = {"灯塔": "lighthouse", "悬崖": "cliff", "风暴": "storm",
                  "指南针": "compass", "渔船": "fishing boat"}
        hit = sum(1 for k in themes if k in out)
        say(f"  theme coverage: {hit}/{len(themes)}")
        if hit < len(themes):
            problems.append(f"long text lost themes ({hit}/{len(themes)})")
    except Exception as exc:
        say(f"  BAD: {type(exc).__name__}: {exc}")
        problems.append("long text translation failed")

    say("")
    say("--- degeneracy guard ---")
    rep = " ".join(f"Marker{i} records observation {i} at station {i*7}."
                   for i in range(1, 31))
    try:
        out = ENGINE.translate(rep, "en", "zh")
        say(f"  repetitive input: {len(rep)} chars -> {len(out)} chars, "
            f"degenerate={is_degenerate(out)}")
    except Exception as exc:
        say(f"  BAD: {type(exc).__name__}: {exc}")
        problems.append("repetitive input raised")

    say("")
    if problems:
        say(f"SELFTEST FAILED ({len(problems)}):")
        for p in problems:
            say(f"  - {p}")
    else:
        say("SELFTEST PASSED")
    log.info("selftest finished, problems=%s", problems)

    # windowed exe has nowhere to print: persist the report next to the log
    try:
        with open(os.path.join(LOG_DIR, "selftest.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return _selftest()
    if "--version" in argv:
        try:
            print(f"ctranslate2 {ctranslate2.__version__}")
        except Exception:
            pass
        return 0

    log.info("start: app_dir=%s data_root=%s cache=%s portable=%s engine=%s minisbd=%s",
             APP_DIR, DATA_ROOT, CACHE_DIR, IS_PORTABLE, HAS_ENGINE, HAS_MINISBD)

    # 上次清理时被占用、只改了名的残留，现在进程刚起、句柄还没被占，正好清掉
    try:
        swept = CACHE.sweep_deferred()
        if swept:
            log.info("startup sweep removed %d deferred cache entries", swept)
    except Exception:
        log.exception("startup cache sweep failed")

    try:
        app = TranslateApp()
    except Exception:
        log.exception("UI startup failed")
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "启动失败",
                f"程序无法启动。\n\n详细日志：{LOG_FILE}\n\n"
                + traceback.format_exc(limit=3))
            root.destroy()
        except Exception:
            pass
        return 1
    try:
        app.run()
    except Exception:
        log.exception("runtime crash")
        return 1
    log.info("exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
