# -*- coding: utf-8 -*-
"""Replace the UI section (Tk class) of app.py with the redesigned one.

The file is split so the engine/paths/settings sections stay untouched and the
new UI can be authored as a standalone file.
"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import _paths
APP = _paths.find_app_source()
UI = os.path.join(_paths.HERE, "ui_section.py")
MARK = "# ======================================================================\n# 8. 入口"

app = open(APP, encoding="utf-8").read()
ui = open(UI, encoding="utf-8").read()

# The UI block starts at the "7. 界面" banner and ends where section 8 begins.
start = app.index("# ======================================================================\n# 7. 界面")
end = app.index(MARK)

old = app[start:end]
print(f"replacing {len(old)} chars of UI with {len(ui)} chars")

new = app[:start] + ui.rstrip("\n") + "\n\n\n" + app[end:]
open(APP, "w", encoding="utf-8").write(new)

# sanity: still exactly one class, one main, balanced
for needle, want in (("class TranslateApp", 1), ("def main(", 1),
                     ("def _selftest(", 1), ("def run(self)", 1)):
    got = new.count(needle)
    flag = "ok " if got == want else "BAD"
    print(f"  {flag} {needle!r}: {got} (want {want})")

import ast
ast.parse(new)
print("  ok  file parses as valid Python")
print(f"  new length: {len(new)} chars, {new.count(chr(10))+1} lines")
