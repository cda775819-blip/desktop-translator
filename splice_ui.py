# -*- coding: utf-8 -*-
"""Replace the UI section (Tk class) of app.py with the redesigned one.

The file is split so the engine/paths/settings/cache/history sections stay
untouched and the UI can be authored as a standalone file.

Locates sections by name, never by number: adding a section renumbers the
headings, and a hardcoded "# 7. 界面" breaks the moment that happens (it did).
"""
import ast
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths
import _splice_common

APP = _paths.find_app_source()
UI = os.path.join(_paths.HERE, "ui_section.py")

RE_UI = _splice_common.section("界面")
RE_ENTRY = _splice_common.section("入口")

app = open(APP, encoding="utf-8").read()
ui = open(UI, encoding="utf-8").read()

start_m = RE_UI.search(app)
end_m = RE_ENTRY.search(app)
if not start_m or not end_m:
    sys.exit(f"找不到段落标记（界面={bool(start_m)} 入口={bool(end_m)}）")
if end_m.start() <= start_m.start():
    sys.exit("段落顺序异常：入口 在 界面 之前")

old = app[start_m.start():end_m.start()]
print(f"replacing {len(old)} chars of UI with {len(ui)} chars")

new = app[:start_m.start()] + ui.rstrip("\n") + "\n\n\n" + app[end_m.start():]
new = _splice_common.renumber(new)
open(APP, "w", encoding="utf-8").write(new)

for needle, want in (("class TranslateApp", 1), ("def main(", 1),
                     ("def _selftest(", 1), ("def run(self)", 1),
                     ("class HistoryStore", 1), ("class CacheManager", 1)):
    got = new.count(needle)
    flag = "ok " if got == want else "BAD"
    print(f"  {flag} {needle!r}: {got} (want {want})")

ast.parse(new)
print("  ok  app.py 仍然是合法 Python")
print(f"  {len(new)} 字符, {new.count(chr(10))+1} 行")
