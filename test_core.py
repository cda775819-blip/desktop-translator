# -*- coding: utf-8 -*-
"""Offline unit tests for the rewritten app: language detection, engine routing,
path handling. Does NOT start the Tk UI. Only the isolated check touches the
network, and it is stubbed to fail."""
import importlib.util
import os
import subprocess
import sys
import time
import io
import textwrap

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import _paths

APP = _paths.find_app_source()
app = _paths.load_app("translator_app")

failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label:<50} got={got!r} want={want!r}")
    if not ok:
        failures.append(label)


def check_true(label, cond, detail=""):
    """用于"条件成立即通过"的断言（check 是等值比较，用不上）。"""
    print(f"  {'PASS' if cond else 'FAIL'}  {label:<50} {detail}")
    if not cond:
        failures.append(label)


print("=== paths ===")
print("  APP_DIR     :", app.APP_DIR)
print("  DATA_ROOT   :", app.DATA_ROOT)
print("  PACKAGES_DIR:", app.PACKAGES_DIR)
print("  portable    :", app.IS_PORTABLE)
app.DATA_ROOT.encode("ascii")
print("  ascii ok    : True")
print("  HAS_ENGINE  :", app.HAS_ENGINE, "| langdetect:", app.HAS_LANGDETECT)
check("engine available", app.HAS_ENGINE, True)

# 回归守卫：曾经把 os.environ["ARGOS_PACKAGES_DIR"] = PACKAGES_DIR 这行误删过。
# 后果是 argostranslate 跑去看 XDG_DATA_HOME 下的空目录，100 个模型一个都认不出来
# （自检显示 "installed: 0 models"）而且**不报任何错**。
# 只断言"引擎能扫到模型"不够 —— 开发机上恰好有 junction 兜住；
# 必须直接断言这个环境变量本身。
print("\n=== ARGOS_PACKAGES_DIR 必须指向 PACKAGES_DIR ===")
_env_dir = os.environ.get("ARGOS_PACKAGES_DIR")
check_true("环境变量已设置", bool(_env_dir), repr(_env_dir))
check_true("环境变量指向 PACKAGES_DIR",
           bool(_env_dir) and os.path.normcase(os.path.abspath(_env_dir))
           == os.path.normcase(os.path.abspath(app.PACKAGES_DIR)),
           f"env={_env_dir!r}")
if app.HAS_ENGINE:
    import argostranslate.settings as _argos_settings
    check_true("argostranslate 读到同一个目录",
               os.path.normcase(os.path.abspath(str(_argos_settings.package_data_dir)))
               == os.path.normcase(os.path.abspath(app.PACKAGES_DIR)),
               f"settings={_argos_settings.package_data_dir}")

print("\n=== language detection ===")
cases = [
    ("Hello world, this is a test of the translator.", "en"),
    ("\u4f60\u597d\uff0c\u4e16\u754c\uff01\u8fd9\u662f\u4e00\u6b21\u79bb\u7ebf\u7ffb\u8bd1\u6d4b\u8bd5\u3002", "zh"),
    ("\u9019\u662f\u7e41\u9ad4\u4e2d\u6587\u7684\u6e2c\u8a66\u53e5\u5b50\u3002", "zh"),
    ("\u3053\u308c\u306f\u65e5\u672c\u8a9e\u306e\u30c6\u30b9\u30c8\u3067\u3059\u3002", "ja"),
    ("\uc548\ub155\ud558\uc138\uc694, \uac83\uc740 \ud14c\uc2a4\ud2b8\uc785\ub2c8\ub2e4.", "ko"),
    ("\u041f\u0440\u0438\u0432\u0435\u0442, \u044d\u0442\u043e \u0442\u0435\u0441\u0442 \u043f\u0435\u0440\u0435\u0432\u043e\u0434\u0430.", "ru"),
    ("\u0645\u0631\u062d\u0628\u0627\u060c \u0647\u0630\u0627 \u0627\u062e\u062a\u0628\u0627\u0631 \u0627\u0644\u062a\u0631\u062c\u0645\u0629.", "ar"),
    ("\u0e2a\u0e27\u0e31\u0e2a\u0e14\u0e35\u0e19\u0e35\u0e48\u0e04\u0e37\u0e2d\u0e01\u0e32\u0e23\u0e17\u0e14\u0e2a\u0e2d\u0e1a", "th"),
    ("\u0393\u03b5\u03b9\u03b1 \u03c3\u03bf\u03c5 \u03ba\u03cc\u03c3\u03bc\u03b5", "el"),
    ("Bonjour, ceci est un test de traduction.", "fr"),
    ("Hola, esta es una prueba de traduccion.", "es"),
    ("Guten Tag, dies ist ein Ubersetzungstest.", "de"),
]
for text, want in cases:
    check(text[:32], app.detect_language(text), want)

print("\n=== detection edge cases ===")
check("empty -> en", app.detect_language(""), "en")
check("whitespace -> en", app.detect_language("   "), "en")
check("digits only -> en", app.detect_language("12345 67890"), "en")
check("very short latin -> en", app.detect_language("ok"), "en")

print("\n=== language table ===")
check("nb present (Argos code)", "nb" in app.LANG_CODES, True)
check("'no' absent", "no" in app.LANG_CODES, False)
check("pb present", "pb" in app.LANG_CODES, True)
check("zt present", "zt" in app.LANG_CODES, True)
check("50 languages", len(app.LANG_CODES), 50)
check("names match codes", sorted(app.LANG_NAMES) == sorted(app.LANG_CODES), True)

print("\n=== engine routing ===")
eng = app.ENGINE
pairs = eng.installed_pairs()
print(f"  installed pairs: {len(pairs)}")
check("en->zh installed", ("en", "zh") in pairs, True)
check("zh->en installed", ("zh", "en") in pairs, True)
check("can translate en->zh", eng.can_translate("en", "zh"), True)
check("can translate ja->zh (pivot)", eng.can_translate("ja", "zh"), True)
check("can translate zh->ja (pivot)", eng.can_translate("zh", "ja"), True)
check("missing links en->zh", eng.missing_links("en", "zh"), [])
check("route en->zh direct", eng._route("en", "zh", pairs), ["en", "zh"])
check("route same lang", eng._route("zh", "zh", pairs), ["zh"])
check("route impossible", eng._route("ja", "zh", {("en", "zh")}), [])

saved = set(eng._installed)
eng._installed = {("en", "zh"), ("zh", "en")}
eng._scanned = True
check("simulated missing ja->zh", eng.missing_links("ja", "zh"), [("ja", "en")])
check("simulated missing en->ja", eng.missing_links("en", "ja"), [("en", "ja")])
check("simulated can_translate ja->zh", eng.can_translate("ja", "zh"), False)
eng._scan_installed(force=True)
check("rescan restores all", len(eng.installed_pairs()), len(saved))

print("\n=== offline translate (no network) ===")
for src, tgt, text in [
    ("en", "zh", "Good morning! How are you today?"),
    ("zh", "en", "\u4f60\u597d\uff0c\u4e16\u754c\uff01"),
    ("ja", "zh", "\u3053\u308c\u306f\u30c6\u30b9\u30c8\u3067\u3059\u3002"),
    ("en", "ja", "This is a test."),
    ("en", "zh", "Line 1 is a test. Line 2 is also a test. Line 3 ends it."),
]:
    try:
        out = eng.translate(text, src, tgt)
        ok = bool(out.strip()) and out.strip() != text.strip()
        print(f"  {'PASS' if ok else 'FAIL'}  {src}->{tgt}: {out!r}")
        if not ok:
            failures.append(f"translate {src}->{tgt}")
    except Exception as exc:
        print(f"  FAIL  {src}->{tgt}: {type(exc).__name__}: {exc}")
        failures.append(f"translate {src}->{tgt}")

print("\n=== long-text completeness (must not silently truncate) ===")
import re as _re
src_text = " ".join(f"Line {i} is a numbered sentence used to check completeness."
                    for i in range(1, 31))
out = eng.translate(src_text, "en", "zh")
present = {int(n) for n in _re.findall(r"\d+", out)}
for i in range(1, 31):
    if i not in present and (f"{i}十" in out or (i == 19 and "十九" in out)):
        present.add(i)
nums = sorted(present & set(range(1, 31)))
print(f"  input {len(src_text)} chars -> output {len(out)} chars")
print(f"  sentence numbers surviving: {len(nums)}/30")
if len(nums) < 30:
    print(f"  FAIL  text was lost (missing {sorted(set(range(1,31)) - present)})")
    failures.append("long text truncated")
else:
    print("  PASS  no content lost")

print("\n=== settings round-trip ===")
s = app.load_settings()
s2 = dict(s)
s2["tgt_lang"] = "ja"
app.save_settings(s2)
check("persisted tgt_lang", app.load_settings()["tgt_lang"], "ja")
s2["tgt_lang"] = "zh"
app.save_settings(s2)
check("restored tgt_lang", app.load_settings()["tgt_lang"], "zh")

print("\n=== isolated: missing models + no network must fail fast ===")
# 子进程也走 _paths 解析：PYTHONPATH 指向本目录，TRANSLATOR_APP 传绝对路径，
# 所以仓库放在哪个盘都能跑（原来这里硬编码 D:\Translator\app.py）。
isolated = textwrap.dedent('''
    import io, sys, time, urllib.error
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    import _paths
    app = _paths.load_app("ta")
    app.ENGINE._installed = set()
    app.ENGINE._scanned = True
    app.ENGINE._available = None
    print("can_translate(en,zh):", app.ENGINE.can_translate("en", "zh"))
    import argostranslate.package as ap
    def boom(*a, **k):
        raise urllib.error.URLError("simulated offline")
    ap.update_package_index = boom
    t0 = time.time()
    try:
        app.ENGINE.translate("hello", "en", "zh")
        print("RESULT: no-exception")
    except app.NoModelError as e:
        print("RESULT: NoModelError in %.2fs" % (time.time()-t0))
        print("MESSAGE:", str(e).replace("\\n", " | ")[:160])
    except Exception as e:
        print("RESULT: %s in %.2fs: %s" % (type(e).__name__, time.time()-t0, e))
''')
_sub_env = dict(os.environ)
_sub_env["PYTHONPATH"] = os.pathsep.join(
    [_paths.HERE] + ([_sub_env["PYTHONPATH"]] if _sub_env.get("PYTHONPATH") else []))
_sub_env["TRANSLATOR_APP"] = APP
_models = _paths.find_model_dir()
if _models:
    _sub_env["ARGOS_PACKAGES_DIR"] = _models

t0 = time.time()
proc = subprocess.run([sys.executable, "-c", isolated], capture_output=True,
                      text=True, encoding="utf-8", errors="replace", timeout=300,
                      env=_sub_env)
print(f"  subprocess wall time: {time.time()-t0:.1f}s")
for line in proc.stdout.splitlines():
    if line.strip():
        print("   ", line.strip())
if "RESULT: NoModelError" not in proc.stdout:
    failures.append("offline path did not raise NoModelError")
    print("    stderr tail:", proc.stderr.strip().splitlines()[-3:])
else:
    secs = float(proc.stdout.split("NoModelError in ")[1].split("s")[0])
    if secs >= 30:
        failures.append("offline error too slow")
    else:
        print(f"  PASS  clear error in {secs:.2f}s")

print("\n" + "=" * 64)
if failures:
    print(f"FAILURES ({len(failures)}):")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL TESTS PASSED")
