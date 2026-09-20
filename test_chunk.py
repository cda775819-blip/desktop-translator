# -*- coding: utf-8 -*-
"""Unit tests for split_for_translation + is_degenerate.

The invariant that matters: concatenating the chunks must reproduce the original
text (modulo whitespace), and no chunk may exceed the budget.
"""
import importlib.util
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 路径解析见 _paths.py（本仓库不假设固定的绝对路径）
import _paths
app = _paths.load_app("ta")

split = app.split_for_translation
degen = app.is_degenerate
failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not cond:
        failures.append(label)


def norm(s):
    return re.sub(r"\s+", "", s)


def budget(s):
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


CASES = {
    "英文散文": "The old lighthouse stood on the cliff. Every evening its beam "
                "swept the water. Sailors saw it from far away. " * 6,
    "中文无空格": "第一句话。第二句话!第三句话?第四句话;第五句话。" * 8,
    "中英混杂": "这是 mixed 文本 with English words。第二句 also mixed 内容。" * 6,
    "无标点长串": "x" * 500,
    "超长单句": "a" * 300 + "。" + "短句。" * 5,
    "大量短句": "Hi. " * 200,
    "换行分隔": "\n".join(f"Line {i} of the document." for i in range(1, 60)),
    "只有空白": "   \n\t  ",
    "单个字符": "A",
    "单个汉字": "好",
    "URL 列表": "\n".join(f"https://example.com/path/segment{i}/index.html"
                         for i in range(1, 20)),
}

print("=== split_for_translation 不变量 ===")
for max_chars in (100, 200, 800):
    print(f"\n--- max_chars={max_chars} ---")
    for name, text in CASES.items():
        chunks = split(text, max_chars)
        # 1) 内容不丢
        same = norm("".join(chunks)) == norm(text)
        # 2) 不超预算
        over = [c for c in chunks if budget(c) > max_chars]
        # 3) 非空块
        empties = [c for c in chunks if not c.strip()]
        ok = same and not over and not empties
        detail = ""
        if not same:
            detail += "内容不一致! "
        if over:
            detail += f"{len(over)} 块超预算(最大 {max(budget(c) for c in over)}) "
        if empties:
            detail += f"{len(empties)} 个空块 "
        if text.strip():
            detail += f"{len(chunks)} 块"
        check(f"{name:<10} [max={max_chars}]", ok, detail)

print("\n=== 单句不被拆断（有标点时）===")
t = "First sentence here. Second sentence here. Third sentence here."
cs = split(t, 60)
check("块尾都是句末标点", all(c.rstrip()[-1] in ".!?。！？；;" for c in cs),
      f"{[c[-1] for c in cs]}")

print("\n=== is_degenerate ===")
check("空字符串", degen("") is False)
check("正常短句", degen("这是一个正常的翻译结果。") is False)
check("正常长文", degen("旧灯塔在悬崖上站了一百二十年,每晚它的梁像一只缓慢的手横扫水面。" * 2) is False)
check("复读机退化", degen("站台" * 60) is True)
check("英文复读", degen("station " * 30) is True)
check("恰好边界", degen("ab" * 3) is False)

print("\n=== 真实退化样本（probe 里实际捕获的模型输出）===")
real_loop = ("Marker1记录观测站1号台站台站点 Marker2记录观测站2号台站点 "
             "Marker3记录观测站3号台站台" + "站台" * 200)
check("尾部复读被识别", degen(real_loop) is True)
# 这份是 probe 里真实的自然散文输出，长度正常
natural = ("旧灯塔在悬崖上站了一百二十年,每晚它的梁像一个缓慢的耐心的手一样横扫着水面,"
           "航海家们说,他们在晴朗的夜晚从三十英里外可以看到它,当暴风雨来临的时候,"
           "守门人拒绝离开他的岗位,波浪吹破了下面的岩石,发出远雷的声音,"
           "当晚他写了三封信,虽然他从未张贴过任何一封信,到了早上,风已经下,"
           "大海几乎是平坦和灰色的,中午时有一艘渔船出现,没有灯光或船员,"
           "守门人划去,只发现一个破折的指南针,他把指南针放在他的窗台上,直到余生为止。")
check("自然散文输出不误报", degen(natural) is False, f"{len(natural)} 字符")
check("中段复读也能发现", degen("正常开头。" + "站台" * 300 + "正常结尾。") is True)

print("\n" + "=" * 58)
if failures:
    print(f"FAILURES ({len(failures)}):")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("分段与退化检测测试全部通过")
