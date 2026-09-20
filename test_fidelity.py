# -*- coding: utf-8 -*-
"""Fidelity test with 40 genuinely distinct sentences, across chunk boundaries.

Repetitive test text makes sentence-count ratios meaningless (and triggers NMT
degeneration), so this uses unique natural prose and checks that every sentence's
subject matter survives, plus the chunk-boundary invariant.
"""
import importlib.util
import io
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 路径解析见 _paths.py（本仓库不假设固定的绝对路径）
import _paths
app = _paths.load_app("ta")

SENTENCES = [
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
    "Visitors sometimes asked why he had never married, and he would smile.",
    "The town grew slowly, then quickly, and the cliff path became a road.",
    "Children dared each other to run up and knock on the lighthouse door.",
    "In the summer of that year a young woman arrived with a notebook.",
    "She stayed four months and wrote down everything the keeper told her.",
    "Her book was published quietly and sold fewer than two hundred copies.",
    "Decades later a historian found it in a secondhand shop for fifty pence.",
    "She read it in one sitting and took the first train to the coast.",
    "The lighthouse had been automated long before, and the cottage stood empty.",
    "But the windowsill still held a compass, green with salt and time.",
    "The historian photographed it and wrote an article for a local paper.",
    "Readers wrote in to say they remembered the keeper, or thought they did.",
    "One letter came from a woman who claimed to be his granddaughter.",
    "She enclosed a photograph of him beside a boat, squinting into the sun.",
    "Nobody could prove anything, and perhaps that was the point of it.",
    "The lighthouse still stands, though the light was switched off years ago.",
    "Tourists park below it and take pictures without knowing any of this.",
    "The sea goes on doing what it has always done, patient and indifferent.",
    "And the wind still smells of salt, exactly as on that October night.",
    "Some stories refuse to end, no matter how quietly they are told.",
    "A gull landed on the rail and watched him fold the letter away.",
    "The lamp room smelled of brass polish and cold paraffin.",
    "He counted the steps twice, as he had every night for forty years.",
    "Below, the tide pulled at the weed with a sound like breathing.",
    "She asked whether he had ever been afraid, and he said only once.",
    "The fog came in so thick that the beam turned back upon itself.",
    "By dawn the glass was streaked with salt and the horizon was clean.",
    "He never learned her surname, and she never asked for his.",
    "The last entry in his log book was a single word, underlined twice.",
    "Years afterwards, someone painted the door the colour of the sea.",
]
text = " ".join(SENTENCES)
print(f"输入: {len(text)} 字符, {len(SENTENCES)} 句 (全部不重复)")

chunks = app.split_for_translation(text, app.Engine.CHUNK_CHARS)
print(f"分块: {len(chunks)} 块, 大小 {[len(c) for c in chunks]}")

# 分块不变量
rebuilt = re.sub(r"\s+", "", "".join(chunks))
orig = re.sub(r"\s+", "", text)
print(f"拼接还原原文: {rebuilt == orig}")
assert rebuilt == orig, "分块丢失或重复了内容"
print(f"最大块计费长度: "
      f"{max(sum(2 if ord(c) > 0x2E80 else 1 for c in c) for c in chunks)} "
      f"(上限 {app.Engine.CHUNK_CHARS})")

msgs = []
t0 = time.time()
out = app.ENGINE.translate(text, "en", "zh", lambda m: msgs.append(m))
dt = time.time() - t0
print(f"\n翻译: {len(text)} 字符 -> {len(out)} 字符, {dt:.1f}s "
      f"({len(text)/dt:.0f} 字符/秒)")
print(f"进度消息: {msgs}")
print(f"退化: {app.is_degenerate(out)}")

zh_sent = len([x for x in re.split(r"[。！？；.!?;]", out) if x.strip()])
print(f"输入 {len(SENTENCES)} 句 -> 输出 {zh_sent} 句")
ratio = zh_sent / len(SENTENCES)
print(f"句数比 {ratio:.2f}")

# 关键主题词是否都在（中文输出里应有对应概念）
THEMES = {
    "灯塔": "lighthouse", "悬崖": "cliff", "风暴/暴风雨": "storm",
    "指南针": "compass", "渔船": "fishing boat", "窗户/窗台": "windowsill",
    "历史学家": "historian", "游客": "tourists", "孙女": "granddaughter",
    "日记/日志": "log book",
}
print("\n主题词覆盖:")
hit = 0
for zh, en in THEMES.items():
    found = any(k in out for k in zh.split("/"))
    hit += found
    print(f"  {'ok  ' if found else '缺失'} {en:<14} ({zh})")
print(f"覆盖 {hit}/{len(THEMES)}")

print("\n--- 输出全文 ---")
print(out)

failures = []
if rebuilt != orig:
    failures.append("分块丢内容")
if app.is_degenerate(out):
    failures.append("输出退化")
if ratio < 0.7:
    failures.append(f"句数比过低 {ratio:.2f}")
if hit < len(THEMES) * 0.8:
    failures.append(f"主题词覆盖不足 {hit}/{len(THEMES)}")
print("\n" + "=" * 58)
if failures:
    print("FAILURES:", failures)
    sys.exit(1)
print("长文保真度测试通过")
