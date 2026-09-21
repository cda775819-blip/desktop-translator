# -*- coding: utf-8 -*-
"""Insert/replace the cache-management section in app.py.

Mirrors splice_ui.py: the section is authored standalone so it can be tested in
isolation, then pasted into app.py between the settings section and the rest.

Idempotent and renumber-agnostic: headings are matched by name, not by number,
because re-running the splice script renumbers them.
"""
import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths
import _splice_common

APP = _paths.find_app_source()
SEC = os.path.join(_paths.HERE, "cache_section.py")

RE_HIST = _splice_common.section("翻译历史")
RE_UI = _splice_common.section("界面")
RE_CACHE = _splice_common.section("缓存管理")

app = open(APP, encoding="utf-8").read()
sec = open(SEC, encoding="utf-8").read()

# 插在"历史"之前；没有历史就插在"界面"之前
anchor = RE_HIST.search(app) or RE_UI.search(app)
if not anchor:
    sys.exit("找不到锚点（翻译历史 / 界面 段落标记）")

# 已经插过就先摘掉旧的缓存段落
m = RE_CACHE.search(app)
if m and m.start() < anchor.start():
    app = app[:m.start()] + app[anchor.start():]
    anchor = RE_HIST.search(app) or RE_UI.search(app)
    print("replace: 找到旧的缓存段落，已移除")

new = app[:anchor.start()] + sec.rstrip("\n") + "\n\n\n" + app[anchor.start():]

# 统一编号（和历史段落脚本共用同一套顺序表，否则两边会算出同一个号）
new = _splice_common.renumber(new)

open(APP, "w", encoding="utf-8").write(new)

for needle, want in (("class CacheManager", 1), ("def clear(", 1),
                     ("CACHE = CacheManager", 1), ("class TranslateApp", 1),
                     ("def main(", 1)):
    got = new.count(needle)
    flag = "ok " if got == want else "BAD"
    print(f"  {flag} {needle!r}: {got} (want {want})")

# 缓存段落必须排在历史之前（历史要用它的 _defer）
i_def = new.find("def _defer(")
i_hist = new.find("class HistoryStore")
if i_hist > 0:
    print(f"  {'ok ' if i_def < i_hist else 'BAD'} _defer({i_def}) < "
          f"HistoryStore({i_hist})")

ast.parse(new)
print("  ok  app.py 仍然是合法 Python")
print(f"  {len(new)} 字符, {new.count(chr(10))+1} 行")
