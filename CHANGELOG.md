# 桌面翻译助手 — 重构改动对比

> 本文档面向其他 AI 或工程师，**便于接手继续改**。所有数字都是在目标机器上实测的，
> 每条结论后面都附了"怎么验证的"，不是估计值。
>
> 技术栈：Python 3.13 / Tkinter / Argos Translate 1.11.0 / CTranslate2 4.8 / PyInstaller 6.22
> 部署形态：Windows x64 免安装 onedir 打包（`D:\Translator\桌面翻译助手.exe`）

---

## 0. TL;DR

把一个"用 bat 启动、依赖 Anaconda、开一次要 50 秒"的脚本，改成
"双击即开、自带运行时、纯离线、0.8 秒出窗口"的独立程序。过程中修掉了
**6 个真 bug** 和 **3 个上游库的设计缺陷**，并纠正了 **2 个我自己先前的错误判断**。

| 指标 | 改造前 | 改造后 | 倍数 |
|---|---|---|---|
| 启动到窗口出现 | ~50 s | **0.8 s** | 62× |
| `import ctranslate2` | 37.6 s | **0.26 s** | 145× |
| `import argostranslate.translate` | 50.8 s | **0.92 s** | 55× |
| 首次翻译 | 56 s（现场下载分句模型） | **0.9 s** | 62× |
| 长文吞吐 | 不可用（退化/慢） | **146–388 字符/秒** | — |
| 打包体积 | 估算 1.5 GB+（含 torch/stanza） | **181 MB** | — |
| 依赖 | 系统 Python + 100+ 三方包 | 无（自带运行时） | — |

---

## 1. 原始代码

两个近似重复的版本：

| 文件 | 说明 |
|---|---|
| `D:\程序\desktop_translator.py` | 653 行，依赖 argostranslate 默认路径（AppData） |
| `D:\程序\translator-portable\desktop_translator.py` | 569 行，自称"便携版"，实际**跑不起来**（见 §3.2） |

启动方式：`启动翻译助手.bat` → `E:\anaconda3\python.exe "%~dp0desktop_translator.py"`
（bat 依赖硬编码的 Anaconda 路径，换机器就废）

**功能**：左右双栏、自动语言检测、50 种语言、经英文中转、模型按需下载、模型管理对话框。

---

## 2. 性能问题：50 秒启动的两条死依赖链

### 2.1 测量方法

```powershell
python -X importtime -c "import argostranslate.translate" 2>&1 | Sort-Object cumulative
```

### 2.2 结果

```
cumulative     module
─────────────────────────────────────────────────────────
50,871,215 µs  argostranslate.translate
36,906,645 µs  ├── ctranslate2                      ← 37 秒
36,713,019 µs  │   └── ctranslate2.converters
34,311,755 µs  │       └── ctranslate2.converters.transformers
34,306,644 µs  │           └── transformers         ← 34 秒
13,829,118 µs  └── argostranslate.sbd               ← 14 秒
 7,334,835 µs      └── stanza
 6,100,775 µs          └── spacy
 3,238,282 µs              └── IPython
 2,953,914 µs              └── networkx
```

### 2.3 根因

**A. `ctranslate2/converters/__init__.py`（第 8 行）**

```python
from ctranslate2.converters.transformers import TransformersConverter
```

`converters` 是"把 HuggingFace/Fairseq/OpenNMT 模型转成 CTranslate2 格式"的工具集。
**翻译已经转好的模型完全用不到它**，但它在 `ctranslate2/__init__.py` 第 58 行被无条件导入：

```python
from ctranslate2 import converters, models, specs   # 第 58 行
```

于是每次启动都白付 34 秒。

**B. `argostranslate/sbd.py`（第 12 行）**

```python
import stanza          # ← 无条件
from minisbd import SBDetect, models as minisbd_models
```

`stanza` 只在 `ARGOS_CHUNK_TYPE=STANZA` 时才用得到，但 top-level 无条件导入，
连带 spacy / IPython / networkx 一起进来，14 秒。

### 2.4 修复

改上游库文件（**已备份 `.bak`**，见 §7）：

| 文件 | 改动 |
|---|---|
| `ctranslate2/converters/__init__.py` | 把 `TransformersConverter` 改成 PEP 562 模块级 `__getattr__` 惰性导入 |
| `argostranslate/sbd.py` | `stanza` 改为 `_lazy_stanza()` 按需导入；`spacy` 改为 `_lazy_spacy()` |
| `argostranslate/translate.py` | 分句器选择逻辑——**这条最关键，见 §2.5** |

### 2.5 隐藏最深的一条：分句器选择

`argostranslate/translate.py` 的 `PackageTranslation.__init__`：

```python
if settings.chunk_type in [ChunkType.ARGOSTRANSLATE, ChunkType.DEFAULT]:
    if "stanza" in str(pkg.packaged_sbd_path):        # ← 这个分支永远赢
        Sentencizer = StanzaSentencizer
    elif "minisbd" in str(pkg.packaged_sbd_path):
        Sentencizer = MiniSBDSentencizer
    else:
        Sentencizer = MiniSBDSentencizer
```

**用户的 100 个模型包里，每一个都带 `stanza/` 子目录**，所以永远走第一个分支 →
每次翻译都要加载 stanza 模型（走 torch）。这才是"翻译一次 5 秒"的真正原因。

改成优先 MiniSBD（0.2–1 MB/语言，走 onnxruntime，不需要 torch），
stanza 分支加 try/except 降级：

```python
if "minisbd" in str(pkg.packaged_sbd_path):
    Sentencizer = MiniSBDSentencizer
elif "stanza" in str(pkg.packaged_sbd_path):
    Sentencizer = MiniSBDSentencizer      # 优先 MiniSBD，只需 onnxruntime
```

> ⚠️ 注意 `settings.chunk_type` 在 import 期就把 `"DEFAULT"` 映射成了
> `ChunkType.ARGOSTRANSLATE`，所以 `DEFAULT` 和 `ARGOSTRANSLATE` 落到**同一个分支**，
> 不能拆成两个 `elif`（我第一版就踩了这个坑）。

### 2.6 效果

| 项目 | 前 | 后 |
|---|---|---|
| `import ctranslate2` | 37.6 s | **0.26 s** |
| `import argostranslate.translate` | 50.8 s | **0.92 s** |
| 首次翻译（en→zh） | 56 s | **0.9 s** |
| 后续翻译 | 2–5 s | **0.3 s** |

---

## 3. 致命 bug

### 3.1 `ARGOS_CHUNK_TYPE=NONE` 会让 argostranslate 彻底瘫痪

我原本想用 `NONE` 关掉分句器来提速。**实测结果：一个翻译都做不了。**

```python
# translate.py:172-184
if settings.chunk_type in [ChunkType.ARGOSTRANSLATE, ChunkType.DEFAULT]:
    ...
elif settings.chunk_type == ChunkType.STANZA: ...
elif settings.chunk_type == ChunkType.MINISBD: ...
elif settings.chunk_type == ChunkType.SPACY: ...

if Sentencizer is not None:
    self.sentencizer = Sentencizer(pkg)
else:
    raise NotImplementedError()          # ← NONE 走到这里
```

而 `get_installed_languages()` 捕获这个异常的方式是**丢弃整个包**：

```python
except NotImplementedError as e:
    info(f"Skipping package {package_key}: {e}")
    continue
```

实测（`probe_graph.py`）：

```
chunk_type = ChunkType.NONE
total: 100 translate: 100
PackageTranslation ctor: ok=0 fail=100          ← 全丢
en can translate to: ['en']                     ← 只剩自己
get_translation_from_codes('en','zh'): None
```

**结论：`ARGOS_CHUNK_TYPE` 必须保持 `DEFAULT`。** 想要"不分句"必须在应用层自己做，
不能碰这个开关。

### 3.2 模型放在中文路径 → sentencepiece 读不了

`translator-portable` 把模型存在 `D:\程序\translator-portable\models\packages`。
`os.path.isdir()` 是 True，但真正加载时：

```
OSError: Not found:
"D:\????\translator-portable\models\packages\translate-en_zh-1_9\sentencepiece.model"
        ^^^^ 中文被读成 ????
```

`sentencepiece` 的 C++ 层用窄字符 API 打开文件，**中文路径直接失败**。
所以那一版"便携版"看起来目录齐全，实际一个模型都加载不了。

**修复策略**（`app.py` 的 `_pick_data_root`）：

```python
def _pick_data_root(candidate: str) -> str:
    try:
        candidate.encode("ascii")          # 能转 ASCII 就用程序目录
    except UnicodeEncodeError:             # 否则退到 LOCALAPPDATA
        return os.path.join(os.environ.get("LOCALAPPDATA", ...), "Translator")
    return candidate
```

同时把 exe 部署到纯英文路径 `D:\Translator`，并**真的把 9.59 GB 模型移进去**
（不是目录链接，早先版本用过 junction，用户反馈"想搬进来"后就实搬了）。

### 3.3 `get_installed_languages()` 的 `lru_cache` 会让新装的模型永远不出现

```python
@functools.lru_cache()
def get_installed_languages() -> list[Language]: ...
```

装了新模型包之后不 `cache_clear()`，语言图不会更新。修复：

```python
def _rescan(self):
    ...
    argos_translate.get_installed_languages.cache_clear()
    self._translations.clear()
```

### 3.4 工作线程直接操作 Tk 控件

原代码 `_warmup` 在工作线程里直接：

```python
self.status_label.config(text=f"就绪（{loaded} 个模型预加载）", fg=self.GREEN)
```

Tk 不是线程安全的，这会随机崩。**修复**：全部改成 `queue.Queue` + 主线程
`after(60, self._pump)` 轮询消费，工作线程只 `put`。

### 3.5 其他

| 位置 | 问题 | 修复 |
|---|---|---|
| `translator-portable` | `_warmup` **定义了两遍**，第一版（含 `_engine_status` 赋值）被覆盖 | 单一实现 |
| `_download_model_if_needed` | 索引里找不到包时**静默 return**，之后翻译只报"失败"看不出原因 | 抛 `PackageMissingError`，消息含具体语言对 |
| `do_download` | `lambda` 引用后面才定义的 `GREEN`/`RED` | 提前定义 |
| 每次 `_translate` | 都调 `update_package_index()` 联网，断网时干等 | 只在缺模型时联网，且分类捕获 `URLError` |
| 语言表 | 挪威语用了 `no`，Argos 实际是 `nb`；巴西葡语 `pb` 缺失；`zh-tw` 映射被前面的 `if` 吞掉 | 用实测的 50 个代码（见 §6.2） |
| 每次按键 | 主线程跑 `langdetect.detect()`（纯 Python，长文本很慢） | 字符集启发式 + 250 ms 防抖 |
| 预热 | 一次加载 10 个语言对模型（内存 1 GB+） | 只扫模型列表，不预加载模型 |

---

## 4. 我先前判断错的两件事（重要，避免重蹈覆辙）

### 4.1 误判"长文本被截断"

用每句都带编号的合成文本测，看到"丢句"就下了结论。
**实际是检测方法错了**：模型把 `thirty miles` 翻成"三十英里"、`three letters` 翻成"三封信"，
阿拉伯数字消失，我的数字匹配就以为内容丢了。

**教训**：测翻译保真度不能靠数字标记，要看**输出句数**和**主题词覆盖**。

### 4.2 误判"分段函数有 bug"

测出相邻块有 70 字符重叠，一度以为分段逻辑错了。查下来是**测试文本每 10 句循环一次**，
不同块自然含相同子串。函数本身正确。

**教训**：分段/切分类函数的测试必须用**完全不重复**的文本，并加不变量断言
（拼接后必须还原原文）。现在 `test_chunk.py` 有 40 项这样的断言。

### 4.3 真实发现的：复读机退化

用**重复性**输入（列表、模板句）测时，模型真的会退化：

```
Marker3记录观测站3号台站台站台站台站台站台站台站台站台站台站台站台站台站台站台…
```

解码器卡在一个片段上反复吐，直到撞上解码上限。**自然散文不触发**（实测 462 token 正常），
但列表类内容会。见 §5.3 的处理。

---

## 5. 新架构

### 5.1 启动顺序（`app.py` 顶部，顺序很关键）

```python
# 1) 必须在任何重量级 import 之前
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")   # ctranslate2+onnxruntime 各带一份 libiomp5md.dll
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.pop("HTTP_PROXY", None) ...                   # 离线优先，清代理
os.environ.setdefault("ARGOS_CHUNK_TYPE", "DEFAULT")     # 绝不能是 NONE（§3.1）

# 2) 路径（纯 ASCII 检查）
APP_DIR = _app_dir(); DATA_ROOT = _pick_data_root(APP_DIR)
os.environ["ARGOS_PACKAGES_DIR"] = PACKAGES_DIR          # 必须在 import argostranslate.settings 之前

# 3) 兜底 shim（万一 site-packages 补丁被覆盖）
_install_fast_import_shim()
import ctranslate2

# 4) 引擎 / UI
```

> ⚠️ `_converters_is_lazy()` 里**不能**用 `importlib.util.find_spec("ctranslate2.converters")`
> —— 那会先导入父包，而父包结尾就是 `from ctranslate2 import converters`，
> 等于自己把要躲的东西叫醒了。只能用纯文件读取。

### 5.2 分段翻译（解决输入上限 + 提速）

**实测依据**：

| 项目 | 数值 |
|---|---|
| 模型单次输入上限 | ~512 token（超了截断） |
| 800 字符英文 | = 172 token（安全） |
| 1200 字符 | = 265 token |
| MiniSBD 切段粒度 | 4768 字符只切 23 段，每段数百 token → 超限 |

**实现**（`app.py`）：

```python
CHUNK_CHARS = 800

def split_for_translation(text, max_chars):
    """按整句聚合。CJK 计费长度 = 2（无空格，否则一路拼到超限）。"""
    # 单句超长 → 硬切；否则整句为单位聚合
```

三个不变量（`test_chunk.py` 覆盖，40 项断言）：
1. 拼接后必须还原原文（`re.sub(r"\s+", "", ...)` 后相等）
2. 无块超预算
3. 无空块

**批量 vs 顺序**：实测批量 `translate_batch` **更慢**（357 vs 371 字符/秒）——
模型是算力瓶颈不是调度瓶颈，所以逐块顺序调用。

`intra_threads` 也有讲究（12 核机器，60 块）：

| 配置 | 耗时 |
|---|---|
| `intra_threads=0`（默认）+ `beam=4, nh=4` | 130.5 s |
| `intra_threads=2` + `beam=2, nh=1` | **33.9 s** |

### 5.3 退化检测 + 降级重试

```python
def is_degenerate(text) -> bool:
    """两个信号，命中任一即判定：
       1. 周期性重复——从尾部算"最小周期"，某单元平铺到窗口 60% 以上
       2. 单字符刷屏——同一字占窗口 45% 以上
       多取几个窗口（60/120/240），因为重复段未必落在最后 80 字符里。"""
```

检测到后把该块**再切小重译**（递归到单句），单句都救不回来才保留原文并写日志。

验证：重复文本判 True，自然散文判 False（`test_chunk.py`）。

### 5.4 线程模型

```
主线程 ─── Tk mainloop ─── after(60ms) 轮询 queue ──→ 更新控件
                                  ↑
工作线程 ── ENGINE.translate() ─── put(("ok"|"error"|"progress"|"cancelled", ...))
```

- 进度按**块**报（`翻译中… 3/8`），不是按语言链
- 取消：`threading.Event`，每块开头检查；`TranslationCancelled` 在
  `_translate_one` 里**必须最先捕获**，否则会被宽泛的 `except Exception` 吞掉
  （我踩过这个坑）

### 5.5 错误处理与降级链

| 场景 | 行为 |
|---|---|
| 缺模型，有网 | 拉索引 → 自动下载 → 续译 |
| 缺模型，无网 | **立即**抛 `NoModelError`，消息含具体原因（实测 0.00 s 返回，不干等） |
| 索引里没这个语言对 | `PackageMissingError`，消息含 `X → Y` |
| 模型调用异常 | 记日志 + 向上抛，UI 显示完整错误 + 弹窗 |
| 输出退化 | 切小重译 → 仍失败则保留原文并 `log.warning` |
| 路径含中文 | 自动退到 `%LOCALAPPDATA%\Translator` |
| 窗口尺寸超屏 | 夹取到可用逻辑空间（见 §5.6） |
| 启动崩溃 | 全局捕获，弹窗带日志路径 |

### 5.6 DPI 与窗口几何（容易踩的坑）

```python
_ENABLE_DPI_AWARENESS()      # 必须在 tk.Tk() 之前
```

**关键**：Tk 报的屏幕尺寸是**物理**还是**逻辑**像素，取决于进程 DPI 感知状态。
不换算的话，150% 缩放屏上窗口会比可用空间宽，右半截跑到屏幕外
（用户实际遇到的 bug 就是这个）。

```python
def _screen_logical_size(root):
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    scale = _system_dpi_scale()          # 读注册表 AppliedDPI
    if _DPI_AWARE_OK and scale > 1.0:
        return int(sw / scale), int(sh / scale), scale
    return sw, sh, scale
```

实测环境：**物理 2560×1440 / 缩放 150% / 逻辑 1706×960**。

另外：**保存的窗口尺寸必须夹取**，不能只判"在不在屏幕内"——
`1801x700+601+523` 起点为正但宽度超屏，旧逻辑漏过，这正是用户症状的直接原因。
以及：`tk.Text` 默认 `width=80`（字符），双栏容器因此要求 **2232 px**；
显式给 `width=1, height=1` 后降到 **232 px**。

---

## 6. 打包

### 6.1 PyInstaller 配置（`translator.spec`）

- 模式：`onedir`（`COLLECT(name=".")` 直接写进 distpath，不套一层文件夹）
- `console=False`（windowed）
- 图标：`icon=assets/app.ico`，多分辨率 7 帧
- `runtime_hooks=[rthook_stub_torch.py]`：给 `torch` 塞 stub，
  因为 `ctranslate2/specs/model_spec.py` 第 18 行有 `try: import torch`，
  加了 stub 就没有多余的 ImportError 噪音

**排除清单**（这是体积从 1.5 GB 降到 181 MB 的关键）：

```python
EXCLUDES = [
    "torch", "torchaudio", "torchvision",           # ctranslate2.specs / stanza 导入
    "stanza", "spacy", "thinc", "transformers",     # 分句 / 模型转换专用
    "tokenizers", "sentence_transformers", "datasets",
    "IPython", "ipykernel", "jupyter",              # stanza 的连带依赖
    "matplotlib", "networkx", "sympy", "pandas", "scipy",
    "sklearn", "pytest", "PyQt5", "PyQt6", "PIL", ...
]
```

**必须打包的数据**：
- `cache/minisbd/*.onnx`（43 个，10.8 MB）— MiniSBD 分句模型，预置后首次翻译不联网
- `assets/app.ico`

**保留的依赖**：`ctranslate2`(58.8 MB) / `onnxruntime`(35.8 MB) /
`numpy`(26 MB) / `_tcl_data`+`_tk_data`(3.7 MB) / `sentencepiece` / `sacremoses` / `langdetect`

### 6.2 MiniSBD 模型预置

`minisbd` 默认**首次使用时**从 GitHub 下载 onnx（每语言 ~0.2–1 MB），
且缓存目录默认在 `%LOCALAPPDATA%\Cache\minisbd`。两个改动：

```python
# 改到程序自己的 cache 目录
import minisbd.models as _m
_m.cache_dir = MINISBD_DIR
```

构建期用 `seed_minisbd.py` 预下载 44 个语言（实测 43 个成功，`sw` 不在 MiniSBD 索引里，
会自动 fallback 到 en）。

Argos 代码 → MiniSBD 模型名的映射（照抄 `argostranslate.sbd.LANGUAGE_CODE_MAPPING` + fallback）：

```python
CODE_MAP = {"zt": "zh-hant", "zh": "zh-hans", "pb": "pt",
            "az": "tr", "bn": "hi", "eo": "en", "ms": "en", "tl": "en"}
```

### 6.3 图标

`gen_icon2.py`（**构建期依赖 Pillow，不进包**）：

- 圆角方块 + 靛蓝→青 45° 渐变 + 顶部柔光 + 白色「译」字带投影
- **每个尺寸原生渲染**，只做 3× 超采样抗锯齿

> 早期版本是把 256px 成品 LANCZOS 缩小成小尺寸，24/32px 任务栏图标**糊**。
> 改成原生渲染后，用拉普拉斯算子量锐度：16px 从 70.8 → **112.6**，
> 24px 从 66.4 → **80.1**。小尺寸另加：字号加大到 76%、底色用更深的青（提高白字对比）、
> **不加粗**（`MaxFilter` 会让笔画糊成一团）。
>
> 用 Pillow 之前试过纯 ctypes+GDI（`DrawText` 写 32 位 DIB **不填 alpha 通道**）
> 和 PowerShell+System.Drawing（`New-Object` 解析不了重载），都失败了。

**验证方法**（看不到图的情况下）：
- 拆 PE 的 `.rsrc` 段确认 7 个 `RT_ICON` + 1 个 `RT_GROUP_ICON` 尺寸齐全
- 把帧渲染成 ASCII 字符画确认「译」的 讠+圣 结构真的画出来了
- 拉普拉斯锐度指标

---

## 7. 环境改动（可完整还原）

### 7.1 Anaconda site-packages（保留 `*.bak`）

```
E:\anaconda3\Lib\site-packages\ctranslate2\converters\__init__.py.bak
E:\anaconda3\Lib\site-packages\argostranslate\sbd.py.bak
E:\anaconda3\Lib\site-packages\argostranslate\translate.py.bak
```

还原：删掉当前文件，把 `.bak` 改回来。

### 7.2 `%LOCALAPPDATA%\Cache\minisbd`

放了 43 个 onnx（10.35 MB），给"用 bat 跑 Anaconda 那条路径"用的。
exe 自己用 `D:\Translator\cache\minisbd`，不依赖这里。

### 7.3 目录布局（最终）

```
D:\Translator\                       9.77 GB
├── 桌面翻译助手.exe                    6.1 MB   ← 双击这个
├── _internal\                       175.0 MB  ← 运行时
├── models\packages\                9590 MB  ← 100 个模型包，779 个文件
├── cache\minisbd\                    10.8 MB  ← 43 个分句模型
├── assets\app.ico                             ← 图标
├── config\settings.json                       ← 设置（geometry/topmost/语言）
├── logs\app.log                               ← 运行日志
├── logs\selftest.txt                          ← --selftest 报告
└── app.py                                     ← 源码副本（真源在 translator-build）
```

### 7.4 构建工具链 `D:\translator-build\`

| 文件 | 用途 |
|---|---|
| `app.py` | **真源**（不在 distpath 里，因为 PyInstaller `--clean` 会清 distpath） |
| `ui_section.py` | UI 部分单独维护，`splice_ui.py` 拼接进 app.py |
| `translator.spec` | PyInstaller 配置 |
| `patch_deps.py` | 给任意 site-packages 打那三个补丁（可复现） |
| `seed_minisbd.py` | 预下载 MiniSBD 模型 |
| `gen_icon2.py` | 生成图标 |
| `rthook_stub_torch.py` | 运行时 stub |
| `test_chunk.py` | 分段不变量 + 退化检测（40 项） |
| `test_engine.py` | 引擎链路 / 进度 / 取消 / 边界输入 |
| `test_fidelity.py` | 长文保真度（40 句不重复散文 + 主题词覆盖） |
| `test_core.py` | 功能回归（检测/路由/离线报错） |
| `test_layout.py` | 布局（8 种屏幕/缩放 + 几何重叠） |
| `verify_icon.py` / `verify_exe_icon.py` | 图标结构验证（含手写 PNG 解码） |
| `venv\` | 打包用环境（979 MB，可重建） |

---

## 8. 实测性能（最终）

### 8.1 启动

| 项目 | 耗时 |
|---|---|
| 窗口出现（冷启动） | **0.8 s** |
| `import ctranslate2` | 0.26 s |
| `import argostranslate.translate` | 0.92 s |
| `import app`（完整） | 0.68 s |
| 加载到内存的重量级模块 | 只有 `onnxruntime`（**无 torch / stanza / spacy / transformers**） |

### 8.2 翻译吞吐（en→zh，自然散文）

| 输入 | 块数 | 耗时 | 吞吐 |
|---|---|---|---|
| 702 字符 | 1 | 1.8 s | 388 字符/秒 |
| 2811 字符 | 4 | 13.4 s | 209 字符/秒 |
| 7029 字符 | 10 | 46.6 s | 151 字符/秒 |
| 14059 字符 | 19 | 96.2 s | 146 字符/秒 |

> 吞吐随长度下降是因为每块固定开销占比变小 / 长块解码更慢。
> 实用换算：**1000 字 ≈ 3–6 秒**。没有硬性字数上限，代价是线性等待。

### 8.3 各语言对模型体积（用于按需分发）

| 语言对 | 体积 |
|---|---|
| en↔zh（简体） | 83 MB × 2 |
| en↔zt（繁体） | 82 MB × 2 |
| en↔ja | 130 MB × 2 |
| en↔ko | 128 MB × 2 |
| en↔fr | 79 MB × 2 |
| en↔de | 157 MB × 2 |
| en↔es | 92 / 301 MB |
| en↔ru | 207 / 166 MB |

常用集（中简繁 + 英 + 日 + 韩 双向，8 个包）≈ **845 MB**。

---

## 9. 验证

### 9.1 测试套件

```
OK   test_chunk.py      分段与退化检测（40 项不变量，含 CJK/无标点/超长单句/URL 列表）
OK   test_engine.py     引擎链路 / 进度回调 / 取消 / 边界输入（空/空白/同语言/纯数字/纯标点）
OK   test_fidelity.py   长文保真度（40 句唯一散文，句数比 0.80，主题词 10/10）
OK   test_core.py       功能回归（语言检测 12 例 / 路由 / 离线报错）
OK   test_layout.py     布局（8 种屏幕×缩放，几何重叠检测）
```

### 9.2 exe 内置自检（`--selftest`）

windowed exe 没有控制台，所以结果同时写 `logs\selftest.txt`：

```
frozen        : True          APP_DIR: D:\Translator
path is ASCII : True          installed: 100 models  (0.12s)
engine        : True          sbd models: 43 onnx files
--- detection ---    en/zh/ja/ko 全对
--- translation ---  en->zh 1.0s / zh->en 0.3s / ja->zh 0.7s / en->ja 0.3s
--- long text ---    2811 chars -> 4 chunks [778,775,776,479]
                     chunking is lossless: True
                     progress callbacks: 4
                     theme coverage: 5/5
--- degeneracy ---   repetitive input: 1406 -> 576 chars, degenerate=False
SELFTEST PASSED   (exit 0)
```

### 9.3 判定标准（可复现）

| 检查 | 方法 |
|---|---|
| 启动提速 | `python -X importtime -c "import argostranslate.translate"` |
| 未加载 torch | `[x for x in ('torch','stanza','spacy') if x in sys.modules]` 应为空 |
| 分段无损 | `re.sub(r"\s+","","".join(chunks)) == re.sub(r"\s+","",text)` |
| 图标完整 | 解析 PE `.rsrc`，应有 7 个 `RT_ICON`，尺寸 256/128/64/48/32/24/16 |
| 离线快速失败 | stub 掉 `update_package_index` 抛 URLError，应在 0.00 s 抛 `NoModelError` |
| 窗口不超屏 | `winfo_x/y/width/height` 必须在 `_screen_logical_size()` 范围内 |

---

## 10. 已知限制 / 未做的事

1. **不做划词翻译**（用户明确说场景兼容太多、不需要）
2. **不做安装包**（用户看过体积方案后说算了）— 分发方式是压缩整个
   `D:\Translator` 发过去；**解压路径含中文会失败**（§3.2）
3. **任务栏图标清晰度只能靠眼睛最终确认** — 我已用锐度指标 + PE 结构验证，
   但看不到实际渲染效果
4. 长文吞吐是 CPU 模型的天花板，`intra_threads=2, beam=2, nh=1` 已调优；
   要更快只能换模型或上 GPU（代码已支持：检测到 CUDA 会自动设 `ARGOS_DEVICE_TYPE=cuda`）
5. `ARGOS_CHUNK_TYPE` 必须是 `DEFAULT`，不要"优化"成 `NONE`

---

## 11. 如果接手继续改

**改代码的位置**：`D:\translator-build\app.py`（真源）。
UI 部分单独在 `ui_section.py`，改完跑 `splice_ui.py` 拼接。

**重建流程**：

```powershell
# 1) 打补丁到 site-packages（若被覆盖）
python D:\translator-build\patch_deps.py <site-packages 路径>

# 2) 预下载分句模型（若缺失）
python D:\translator-build\seed_minisbd.py D:\Translator\cache\minisbd

# 3) 生成图标（若改了设计）
D:\translator-build\venv\Scripts\python.exe D:\translator-build\gen_icon2.py D:\Translator\assets\app.ico

# 4) 打包
D:\translator-build\venv\Scripts\pyinstaller.exe --noconfirm --clean `
  --distpath D:\translator-build\dist --workpath D:\translator-build\build `
  D:\translator-build\translator.spec

# 5) 部署（distpath 千万别设成 D:\Translator，--clean 会清空它，我踩过）
Copy-Item D:\translator-build\dist\_internal D:\Translator\_internal -Recurse -Force
Copy-Item "D:\translator-build\dist\桌面翻译助手.exe" D:\Translator\ -Force
Copy-Item D:\translator-build\app.py D:\Translator\app.py -Force

# 6) 验证
Start-Process "D:\Translator\桌面翻译助手.exe" -ArgumentList "--selftest" -Wait
Get-Content D:\Translator\logs\selftest.txt
python D:\translator-build\test_chunk.py; test_engine.py; test_fidelity.py; ...
```

**可以继续做的方向**（按价值排序）：
1. 托盘图标 + 最小化到托盘
2. 翻译历史
3. 剪贴板监听模式
4. 长文增量显示（现在是全部翻完才显示）
5. 多显示器切换时重新夹取窗口（现在只在启动时算一次）
