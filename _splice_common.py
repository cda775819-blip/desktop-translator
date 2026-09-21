# -*- coding: utf-8 -*-
"""Shared helpers for the section-splicing scripts.

The splice scripts locate sections by *name* (never by number, which changes as
sections are added) and then renumber all headings by their real order in the
file. Keeping that in one place matters: when two scripts each renumbered using
their own table, the second one couldn't see the section the first had inserted
and gave both the same number.
"""
from __future__ import annotations

import re

# 段落的标准顺序。拼接脚本按实际出现顺序重新编号，编号只给人看。
SECTION_ORDER = [
    "环境准备", "路径", "日志", "兜底 shim", "引擎", "设置",
    "缓存管理", "翻译历史", "界面", "入口",
]

RE_HEADING = re.compile(r"# (\d+)\. (\S+)")


def renumber(text: str) -> str:
    """按 SECTION_ORDER 重排所有段落编号。"""

    def _sub(match: re.Match) -> str:
        name = match.group(2)
        if name in SECTION_ORDER:
            return f"# {SECTION_ORDER.index(name) + 1}. {name}"
        return match.group(0)

    return RE_HEADING.sub(_sub, text)


def section(name: str) -> re.Pattern:
    """匹配某个段落的标题行（不关心它的编号）。"""
    return re.compile(r"# ={70}\n# \d+\. " + re.escape(name))
