# 桌面翻译助手（离线版）

基于 [Argos Translate](https://github.com/argosopentech/argos-translate) / CTranslate2 的
Windows 桌面翻译工具。**纯离线**，不联网也能翻，不需要装 Python。

- 50 种语言互译，任意语言对自动经英文中转
- 启动 0.8 秒，短句翻译 0.3 秒，长文约 150–400 字符/秒
- 长文自动分段，重复性内容不会触发"复读机"退化
- **翻译历史**：只存本机，可回填到输入框；有容量上限，不会无限长
- **磁盘占用可见可管**：模型 / 缓存 / 历史分三级保留策略，误删不了模型
- 单目录体积 181 MB（不含语言模型）

| | |
|---|---|
| 界面 | Tkinter，深色，卡片布局，DPI 感知 |
| 引擎 | CTranslate2 + SentencePiece + MiniSBD 分句 |
| 打包 | PyInstaller onedir |
| 平台 | Windows x64 |

---

## 下载

程序以 **Release 附件**形式发布（仓库里只有源码 —— 运行时 175 MB、语言模型 9.6 GB，
都超过 GitHub 单文件 100 MB 的限制）。

到 [Releases](../../releases) 页面下载，有两种：

| 包 | 大小 | 说明 |
|---|---|---|
| `*-app.7z.001` / `.002` | 约 180 MB | 程序本体（含运行时）。**不含语言模型**，首次翻译某种语言对时自动下载（每对 80–160 MB，需要联网） |
| `*-models-common.7z` | 约 845 MB | 可选：中简繁 + 英 + 日 + 韩 双向模型，装上后这几门语言全程离线 |

**安装**：解压到同一个目录，让结构长这样，然后双击 exe：

```
任意位置\桌面翻译助手\          ← 路径必须只含 ASCII 字符，见下方"注意"
├── 桌面翻译助手.exe
├── _internal\
├── models\packages\           ← 放语言模型；没有就先空着，运行时会自动下载
├── cache\minisbd\             ← 已在 _internal 里预置，不用管
└── history.jsonl              ← 翻译历史，首次翻译后自动出现
```

> **注意：解压路径不能含中文。** `sentencepiece` 的 C++ 层用窄字符 API 打开文件，
> 路径里有中文会直接 `OSError: Not found`。程序检测到程序目录含非 ASCII 会自动
> 改用 `%LOCALAPPDATA%\Translator` 存数据，但模型放在英文路径下最省事。

---

## 磁盘上的东西分成三级

界面底栏的「缓存管理」把占用摊开，并按**保留等级**决定能不能删：

| 等级 | 内容 | 删除 |
|---|---|---|
| 用户资产 | `models/packages`（翻译模型，最大的一块） | **不提供删除** —— 接口层就没这个可能，不是靠界面自觉 |
| 用户记录 | `history.jsonl`（翻译历史） | 可删，但**不可恢复**，独立按钮 + 单独确认 |
| 可再生 | `cache/minisbd`、索引、`logs` | 随便清，下次用到时自动重建 |

这个分级不是装饰：把历史混进"清理缓存"的话，用户点一下就把自己的翻译记录
连缓存一起清掉了 —— 而历史既不可恢复、也不是缓存。

清理用「删不掉就改名释放空间」的策略：文件被本进程占用时，改名成 `.trash-*`
先把空间让出来，下次启动时清掉，所以清理不会因为占用而失败。

## 翻译历史

- 只存在本机的 `history.jsonl`，不上传任何地方
- 格式是一行一条 JSON：崩在写一半最多损失最后一条，不会毁掉整份历史
- **两条上限同时生效**：200 条 + 8 MB，超了从最旧的淘汰。单条内容完整保存、不截断
- 记录失败**绝不影响翻译**：写入点在翻译成功之后，写不进去只写日志，
  界面照常显示译文（拿附加功能毁掉主功能是最糟的失败方式）
- 界面里可以翻页浏览、双击把原文回填到输入框、或只恢复译文

上限是 `history_section.py` 里的 `DEFAULT_MAX_ENTRIES` / `DEFAULT_MAX_BYTES`。

---

## 从源码运行

```bash
pip install -r requirements.txt
python patch_deps.py <你的 site-packages 路径>   # 见下方"为什么必须打补丁"
python app.py
```

首次运行会在 `app.py` 同级创建 `models/packages`。放模型进去，或在界面里点
「管理模型」→「刷新可下载清单」→「下载并安装」。

> 想跑测试套件的话，`D:\translator-build\models` 和 `cache` 可以做成指向
> 部署目录的目录链接（`mklink /J`），这样源码目录也能找到模型。

### 为什么必须打补丁

`argostranslate` 在 PyPI 上把 `stanza` 和 `spacy` 声明为必需依赖，但翻译**已转好的**
CTranslate2 模型完全用不到它们。它们只在两条路径上需要：

1. `ctranslate2/converters/` —— 把 HuggingFace/Fairseq 模型**转换**成 CTranslate2 格式
2. `argostranslate/sbd.py` 的 stanza 分句器（本应用改用 MiniSBD）

问题是这两处都是 **top-level 无条件导入**，于是一次性的：

```
$ python -X importtime -c "import argostranslate.translate"
50.8s  argostranslate.translate
 ├ 36.9s  ctranslate2
 │   └ 36.7s  ctranslate2.converters
 │       └ 34.3s  ctranslate2.converters.transformers → transformers → torch
 └ 13.8s  argostranslate.sbd → stanza → spacy → IPython / networkx
```

`patch_deps.py` 把这三处改成惰性导入。补丁后会备份原文件为 `*.bak`：

| 文件 | 改动 | 效果 |
|---|---|---|
| `ctranslate2/converters/__init__.py` | `TransformersConverter` 改为 PEP 562 模块级 `__getattr__` | 37.6s → **0.26s** |
| `argostranslate/sbd.py` | `stanza` / `spacy` 改为按需导入 | 14s → 0 |
| `argostranslate/translate.py` | 分句器优先 MiniSBD 而不是包内自带的 stanza | 首次翻译 56s → **0.9s** |

还原：删掉当前文件，把 `.bak` 改回来。

> **不要**把 `ARGOS_CHUNK_TYPE` 设成 `NONE`。它会让
> `PackageTranslation.__init__` 抛 `NotImplementedError`，而
> `get_installed_languages()` 会把这个异常当成"包有问题"**丢弃整个包** ——
> 实测 100 个包全被丢掉（`ok=0 fail=100`），任何翻译都返回 `None`。

---

## 构建 exe

```bash
pip install -r requirements-build.txt

# 1) 预置分句模型（约 11 MB，首次翻译不联网的关键）
python seed_minisbd.py <运行时目录>\cache\minisbd

# 2) 生成图标（可选，仓库里已带 assets/app.ico）
python gen_icon.py assets/app.ico

# 3) 打包
pyinstaller --noconfirm --clean ^
    --distpath <dist> --workpath build ^
    translator.spec

# 4) 验证
"<dist>\桌面翻译助手.exe" --selftest
type <运行时目录>\logs\selftest.txt
```

spec 支持环境变量覆盖：`TRANSLATOR_SRC` / `TRANSLATOR_DATA` / `TRANSLATOR_ASSETS` / `TRANSLATOR_MINISBD`。

> ⚠️ **不要**把 `--distpath` 指向程序自己的运行目录。`--clean` 会清空 distpath ——
> 我因此丢过一次 `app.py`。

### 体积是怎么压下来的

排除 `torch` / `stanza` / `spacy` / `transformers` / `IPython` / `networkx` /
`matplotlib` / `pandas` / `scipy` 后：**181 MB**（含 11 MB 分句模型）。
不排除的话约 1.5 GB。

---

## 测试

```bash
python test_cache.py        # 三级保留策略 / clear 与 purge 的越权拒绝 / 委托删除后状态同步
python test_history.py      # 历史存储：容量淘汰 / 损坏行 / 撕裂行 / emoji / 非 ASCII 路径
python test_history_app.py  # 集成：捕获点、失败与取消不写、写入失败不影响翻译结果
python test_chunk.py        # 分段不变量（40 项）+ 退化检测
python test_engine.py       # 引擎链路 / 进度回调 / 取消 / 边界输入
python test_fidelity.py     # 长文保真度（40 句不重复散文，主题词覆盖）
python test_core.py         # 功能回归（语言检测 / 路由 / 离线报错 / 环境变量守卫）
python test_layout.py       # 布局（8 种屏幕×缩放，几何重叠检测）
```

8 个套件全绿是改动的底线。

`test_core.py` 里有一条容易看漏但很重要的守卫：断言 `ARGOS_PACKAGES_DIR`
必须等于 `PACKAGES_DIR`。这行曾被误删，后果是 argostranslate 跑去看
`XDG_DATA_HOME` 下的空目录，**100 个模型一个都不认识而且不报任何错**
（自检显示 `installed: 0 models`）。只断言"引擎能扫到模型"是不够的 ——
开发机上恰好有目录链接兜住，必须直接断言这个环境变量本身。

测试通过 `_paths.py` 定位 `app.py` 和模型，所以仓库放在任何位置都能跑；
也可以设 `TRANSLATOR_APP` / `ARGOS_PACKAGES_DIR` 显式指定。

`--selftest` 是给打包后的 exe 用的：windowed 程序没有控制台，所以结果同时写到
`logs/selftest.txt`，并包含长文分段、进度回调、退化守护的检查。

---

## 设计要点（踩过的坑）

### 1. 语言模型必须放在纯 ASCII 路径

`sentencepiece` 的 C++ 层用窄字符 API 打开文件：

```
OSError: Not found:
"D:\????\translator-portable\models\packages\...\sentencepiece.model"
```

`os.path.isdir()` 返回 True，但真正加载时中文被读成 `????`。
程序启动时会检测自己的目录，含非 ASCII 就退回 `%LOCALAPPDATA%\Translator`。

### 2. 长文必须自己分段

CTranslate2 模型单次输入有上限（实测约 512 token）。800 字符英文 ≈ 172 token，安全。
不分段的话，`argostranslate` 内部的 MiniSBD 会切出几百 token 的大段（实测 4768 字符
只切 23 段），超限后被静默截断。

分段规则（`split_for_translation`）：
- 按**整句**聚合到 800 字符（不切断句子）
- 中日韩没有词间空格，计费长度按 CJK = 2 算，否则会一路拼到超限
- 单句本身超长（无标点长串、URL 列表）才硬切
- 不变量：**各段拼接必须还原原文**（有测试守着）

顺带把吞吐从 371 提到 566 字符/秒。

### 3. 重复性文本会触发"复读机"退化

每句结构几乎一样的输入（列表、模板句、歌词）会让解码器卡在一个片段上无限重复，
直到撞上解码上限：

```
Marker3记录观测站3号台站台站台站台站台站台站台站台站台站台站台站台站台…
```

自然散文不触发。`is_degenerate()` 用两个信号检测（周期性重复 / 单字符刷屏），
命中就把该段再切小重译，最多递归到单句。

> 附注：用"每句带编号"的合成文本测保真度是**不可靠的** ——
> 模型会把 `thirty miles` 翻成"三十英里"，阿拉伯数字消失，数字匹配会误报"内容丢失"。
> 应该看输出句数和主题词覆盖。

### 4. Tk 不是线程安全的

所有耗时工作在线程里做，结果通过 `queue.Queue` 回主线程 `after(60ms)` 轮询消费。
工作线程绝不碰控件。

### 5. Tk 的 `Text` 默认宽 80 字符

不显式给 `width=1, height=1` 的话，双栏容器的最小宽度会变成 **2232 px**，
窗口一窄右栏就被挤出可视区。

### 6. 窗口几何要按 DPI 换算

Tk 报的屏幕尺寸是物理还是逻辑像素，取决于进程 DPI 感知状态。150% 缩放的
2560×1440 屏上，可用逻辑空间只有 1706×960。不换算 + 不夹取保存的尺寸，
窗口就会比屏幕宽（实测踩到过 `1801x700` 存进配置）。

### 7. 图标要按尺寸原生渲染

把 256px 成品缩小到 24/32px 会让「译」的细笔画被插值抹平，任务栏图标发糊。
改成每个尺寸独立渲染（3× 超采样仅用于抗锯齿）。用拉普拉斯算子量锐度验证：
16px 从 70.8 → 112.6。

---

## 文件说明

`app.py` 是拼装出来的成品：几个段落各自独立成文件（便于单独测试），
由 `splice_*.py` 按顺序拼进去。改代码请改段落文件，然后重新拼接，别直接改 `app.py`。

| 文件 | 说明 |
|---|---|
| `app.py` | **生成的**成品（路径 / 日志 / 引擎 / 设置 / 缓存 / 历史 / 界面 / 自检） |
| `cache_section.py` | 磁盘占用与三级保留策略 → 拼成段落 7 |
| `history_section.py` | 翻译历史存储 → 拼成段落 8 |
| `ui_section.py` | 界面 → 拼成段落 9 |
| `_splice_common.py` | 拼接脚本共用的段落定位与编号 |
| `splice_cache.py` · `splice_history.py` · `splice_ui.py` | 按顺序拼接（顺序不能乱：历史要用缓存的 `_defer`） |
| `_paths.py` | 测试与工具脚本的路径解析（不假设任何绝对路径） |
| `patch_deps.py` | 给 site-packages 打那三个惰性导入补丁 |
| `seed_minisbd.py` | 预下载 MiniSBD 分句模型 |
| `gen_icon.py` | 生成多分辨率图标（需要 Pillow） |
| `translator.spec` | PyInstaller 配置 |
| `rthook_stub_torch.py` | 运行时 stub：`ctranslate2.specs` 会 `try: import torch` |
| `test_cache.py` · `test_history.py` · `test_history_app.py` | 缓存与历史 |
| `test_chunk.py` · `test_engine.py` · `test_fidelity.py` | 分段 / 引擎 / 长文保真度 |
| `test_core.py` · `test_layout.py` | 功能回归 / 布局 |
| `verify_icon.py` · `verify_exe_icon.py` | 图标结构验证（含手写 PNG 解码 / PE 资源遍历） |
| `probe_*.py` | 一次性的测量脚本，保留下来是为了让结论可复现 |
| `CHANGELOG.md` | 完整的改动对比与实测数据 |

---

## 已知限制

- 没有划词翻译 / 剪贴板监听（历史有了）
- 长文要等翻完才显示（没有增量输出）
- 吞吐受 CPU 模型限制：1000 字约 3–6 秒。检测到 CUDA 会自动启用 GPU
  （`ARGOS_DEVICE_TYPE=cuda`），但未在 GPU 机器上验证过
- 多显示器切换后不会重新夹取窗口位置（只在启动时算一次）
- 历史按容量自动淘汰最旧的，没有"只保留最近 N 天"这类时间维度策略

## 许可

代码采用 MIT。语言模型来自
[Argos Translate](https://github.com/argosopentech/argos-translate)（MIT），
分句模型来自 [MiniSBD](https://github.com/LibreTranslate/MiniSBD)。
