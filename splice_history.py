# -*- coding: utf-8 -*-
"""Insert the translation-history section into app.py.

Order matters: this must run AFTER splice_cache.py, because the history section
reuses _defer() from the cache section (they end up in the same module).

Section layout after both splices:
    5. 引擎
    6. 设置
    7. 缓存管理      <- splice_cache.py
    8. 翻译历史      <- this script
    9. 界面
"""
import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths
import _splice_common

APP = _paths.find_app_source()
SEC = os.path.join(_paths.HERE, "history_section.py")

# 用共享的正则匹配段落标题，别写死编号 —— 脚本重跑时会重新编号。
RE_UI = _splice_common.section("界面")
RE_HIST = _splice_common.section("翻译历史")

app = open(APP, encoding="utf-8").read()
sec = open(SEC, encoding="utf-8").read()

# 已经插过就先摘掉
m = RE_HIST.search(app)
if m:
    ui = RE_UI.search(app)
    if ui and ui.start() > m.start():
        app = app[:m.start()] + app[ui.start():]
        print("replace: 找到旧的历史段落，已移除")

ui = RE_UI.search(app)
if not ui:
    sys.exit("找不到 '界面' 段落标记")
before = ui.start()

new = app[:before] + sec.rstrip("\n") + "\n\n\n" + app[before:]

# 统一编号（与缓存段落脚本共用同一套顺序表）
new = _splice_common.renumber(new)

open(APP, "w", encoding="utf-8").write(new)

for needle, want in (("class HistoryStore", 1), ("class HistoryEntry", 1),
                     ("def record(", 1), ("HISTORY = HistoryStore", 1),
                     ("class CacheManager", 1), ("class TranslateApp", 1),
                     ("def main(", 1)):
    got = new.count(needle)
    flag = "ok " if got == want else "BAD"
    print(f"  {flag} {needle!r}: {got} (want {want})")

i_defer = new.find("def _defer(")
i_hist = new.find("class HistoryStore")
ok = 0 < i_defer < i_hist
print(f"  {'ok ' if ok else 'BAD'} _defer 在 HistoryStore 之前 "
      f"({i_defer} < {i_hist})")

for m2 in re.finditer(r"# (\d+)\. (\S+)", new):
    print(f"  段落 {m2.group(1):>2}. {m2.group(2)}")

ast.parse(new)
print("  ok  app.py 仍然是合法 Python")
print(f"  {len(new)} 字符, {new.count(chr(10))+1} 行")
