# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for 桌面翻译助手 (offline desktop translator).

Build (from the repo root):
    pyinstaller --noconfirm --clean ^
        --distpath <dist> --workpath <build> translator.spec

Environment overrides (all optional; sensible defaults derived from this file):
    TRANSLATOR_SRC     path to app.py                 (default: ./app.py)
    TRANSLATOR_DATA    runtime data dir, i.e. the folder that holds
                       models/ cache/ assets/         (default: ../Translator)
    TRANSLATOR_ASSETS  folder containing app.ico      (default: ./assets)

Layout produced in <dist>:
    桌面翻译助手.exe
    _internal\\...                        runtime (Python + ctranslate2 + onnxruntime)
    _internal\\cache\\minisbd\\*.onnx     pre-seeded sentence splitter (~11 MB)
    _internal\\assets\\app.ico            window / taskbar icon

IMPORTANT: never point --distpath at the app's own runtime folder. `--clean`
deletes the distpath, which will take app.py and the models with it.
"""

import os

SPEC = os.path.abspath(SPEC)                      # noqa: F821 (injected by PyInstaller)
SPEC_DIR = os.path.dirname(SPEC)
REPO_ROOT = os.path.dirname(SPEC_DIR)             # translator-build 的上一级

SRC = os.environ.get("TRANSLATOR_SRC", os.path.join(SPEC_DIR, "app.py"))
# 运行时数据目录：models/ cache/ assets/ 都在它下面。
# APP_ROOT 只用来找"构建时要打包的资源"；程序自己运行时是按 exe 位置算的，
# 所以即使 exe 最终被拷到别处也不受影响（除非那个路径含非 ASCII）。
APP_ROOT = os.environ.get("TRANSLATOR_DATA",
                          os.path.join(REPO_ROOT, "Translator"))
MINISBD_SRC = os.environ.get("TRANSLATOR_MINISBD",
                             os.path.join(APP_ROOT, "cache", "minisbd"))
# assets 默认跟着仓库走，这样即使 app.py 被拷到 APP_ROOT，图标也找得到
ASSETS_SRC = os.environ.get("TRANSLATOR_ASSETS", os.path.join(SPEC_DIR, "assets"))
ICON = os.path.join(ASSETS_SRC, "app.ico")

print(f"[spec] SRC      = {SRC}")
print(f"[spec] APP_ROOT = {APP_ROOT}")
print(f"[spec] ICON     = {ICON}")

datas = []
if os.path.isdir(MINISBD_SRC):
    onnx = [f for f in os.listdir(MINISBD_SRC) if f.endswith(".onnx")]
    if onnx:
        datas.append((MINISBD_SRC, "cache/minisbd"))
        print(f"[spec] bundling {len(onnx)} MiniSBD models from {MINISBD_SRC}")
    else:
        print(f"[spec] WARNING: no .onnx in {MINISBD_SRC}")
else:
    print(f"[spec] WARNING: {MINISBD_SRC} missing - "
          f"first translation will need to download the splitter")

if os.path.exists(ICON):
    datas.append((ICON, "assets"))
    print(f"[spec] bundling icon {ICON}")
else:
    print(f"[spec] WARNING: {ICON} missing - window will use the default Tk icon")

# Heavy packages that Argos/CTranslate2 only need for features this app never
# uses. Excluding them is what takes the bundle from ~1.5 GB down to ~180 MB:
#   torch/torchaudio/torchvision : ctranslate2.specs and stanza import it
#   stanza/spacy/thinc           : sentence-boundary detection only (we use MiniSBD)
#   transformers/tokenizers      : model *conversion* only, not inference
#   IPython/networkx/matplotlib  : dragged in by stanza
EXCLUDES = [
    "torch", "torchaudio", "torchvision", "torchtext",
    "stanza", "spacy", "thinc", "transformers", "tokenizers",
    "sentence_transformers", "datasets", "accelerate", "huggingface_hub",
    "IPython", "ipykernel", "jupyter", "jupyter_client", "jupyter_core",
    "nbformat", "nbconvert", "notebook", "ipywidgets",
    "matplotlib", "mpl_toolkits", "networkx", "sympy", "pandas",
    "scipy", "sklearn", "scikit-learn", "pytest", "setuptools", "pip",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
    "cv2", "PIL", "imageio", "plotly", "bokeh",
    "tensorflow", "keras", "onnx", "tensorboard",
    "lib2to3", "idlelib", "turtle", "turtledemo", "test", "distutils",
]

block_cipher = None

a = Analysis(
    [SRC],
    pathex=[SPEC_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "argostranslate.package",
        "argostranslate.translate",
        "argostranslate.settings",
        "argostranslate.sbd",
        "minisbd",
        "minisbd.models",
        "minisbd.inference",
        "langdetect",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(SPEC_DIR, "rthook_stub_torch.py")],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="桌面翻译助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON if os.path.exists(ICON) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=".",          # write straight into --distpath, no wrapper folder
)
