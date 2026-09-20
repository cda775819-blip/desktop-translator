# -*- coding: utf-8 -*-
"""Pre-seed the MiniSBD sentence-splitting models the translator needs.

MiniSBD normally downloads an .onnx per language on first use. We fetch them at
build time into the app's cache dir so the shipped program works with no network.

Argos `pkg.from_code` -> MiniSBD model name. Mapping mirrors
argostranslate.sbd.MiniSBDSentencizer.LANGUAGE_CODE_MAPPING plus its documented
fallbacks (az->tr, bn->hi, eo->en, ms->en, tl->en).
"""
import os
import sys
import time

import _paths

# 依赖 minisbd（构建期安装，见 README 的"构建"一节）
CODE_MAP = {
    "zt": "zh-hant", "zh": "zh-hans", "pb": "pt",
    "az": "tr", "bn": "hi", "eo": "en", "ms": "en", "tl": "en",
}

ARGOS_CODES = [
    "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en", "eo", "es",
    "et", "eu", "fa", "fi", "fr", "ga", "gl", "he", "hi", "hu", "id", "it",
    "ja", "ko", "ky", "lt", "lv", "ms", "nb", "nl", "pb", "pl", "pt", "ro",
    "ru", "sk", "sl", "sq", "sv", "sw", "th", "tl", "tr", "uk", "ur", "vi",
    "zh", "zt",
]


def default_out_dir() -> str:
    """默认写到 app 同级的 cache/minisbd（app 自己也是这么找的）。"""
    if os.environ.get("TRANSLATOR_DATA"):
        return os.path.join(os.environ["TRANSLATOR_DATA"], "cache", "minisbd")
    app = _paths.find_app_source()
    return os.path.join(os.path.dirname(app), "cache", "minisbd")


out_dir = sys.argv[1] if len(sys.argv) > 1 else default_out_dir()
os.makedirs(out_dir, exist_ok=True)

import minisbd.models as models  # noqa: E402

models.cache_dir = out_dir

wanted = sorted({CODE_MAP.get(c, c) for c in ARGOS_CODES})
available = set(models.list_models())
print(f"cache dir : {out_dir}")
print(f"wanted    : {len(wanted)} models")

missing_from_index = [w for w in wanted if w not in available]
if missing_from_index:
    print(f"NOT in MiniSBD index (will fall back to en): {missing_from_index}")

ok, failed = 0, []
for name in wanted:
    if name not in available:
        continue
    try:
        t0 = time.time()
        path = models.get_model_file(name)
        size = os.path.getsize(path) / 1e6
        print(f"  ok    {name:<9} {size:6.2f} MB  ({time.time()-t0:.1f}s)")
        ok += 1
    except Exception as exc:
        print(f"  FAIL  {name:<9} {type(exc).__name__}: {exc}")
        failed.append(name)

total = sum(os.path.getsize(os.path.join(out_dir, f))
            for f in os.listdir(out_dir) if f.endswith(".onnx"))
print(f"\nseeded {ok} models, {len(failed)} failed, total {total/1e6:.1f} MB")
if failed:
    print("failed:", failed)
    sys.exit(1)
